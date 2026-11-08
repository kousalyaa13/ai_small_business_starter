/* BloomBot recommendation UI. Product-first cards, no duplicated tool rows.
   Pure grouping helpers are exported for lightweight Node tests. */
(function (root) {
  function groupTools(option) {
    const map = new Map();
    for (const a of option.assignments || []) {
      if (a.source === 'none') continue;
      const key = a.tool_id || 'tool:' + a.tool_name;
      if (!map.has(key)) {
        map.set(key, {
          id: key, name: a.tool_name, plan: a.plan_name || '',
          site: a.site_url || '', pricingUrl: a.pricing_source_url || a.source_url || '',
          checkedAt: a.pricing_checked_at || '', description: a.tool_description || '',
          cost: 0, costUnknown: false, tasks: [], sources: [], nativeOnly: true,
          vetted: true, costBasis: '', complexity: a.setup_complexity || 'unknown'
        });
      }
      const t = map.get(key);
      if (a.source !== 'native') {
        t.nativeOnly = false;
        t.name = a.tool_name;
        t.plan = a.plan_name || t.plan;
        t.description = a.tool_description || t.description;
        t.costBasis = a.cost_basis || t.costBasis;
        t.complexity = a.setup_complexity || t.complexity;
      }
      t.site = a.site_url || t.site;
      t.pricingUrl = a.pricing_source_url || a.source_url || t.pricingUrl;
      t.checkedAt = a.pricing_checked_at || t.checkedAt;
      t.vetted = t.vetted && !!a.vetted;
      if (a.monthly_cost == null && a.source !== 'native') t.costUnknown = true;
      else t.cost += Number(a.monthly_cost || 0);
      if (!t.tasks.some(x => x.id === a.component_id)) {
        t.tasks.push({id:a.component_id, name:a.description || a.capability.replaceAll('_', ' '),
                      capability:a.capability});
      }
    }
    return [...map.values()].map(t => ({...t, cost: Math.round(t.cost * 100) / 100}));
  }

  function uniqueTasks(option) {
    const seen = new Set();
    return (option.assignments || []).filter(a => {
      if (seen.has(a.component_id)) return false;
      seen.add(a.component_id); return true;
    }).map(a => ({id:a.component_id, name:a.description || a.capability.replaceAll('_', ' '),
                  covered:a.source !== 'none'}));
  }

  function billingLabel(plan) {
    if (/annual billing/i.test(plan || '')) return 'Annual commitment';
    if (/monthly billing/i.test(plan || '')) return 'Billed monthly';
    return '';
  }
  const api = {groupTools, uniqueTasks, billingLabel};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (typeof document === 'undefined') return;

  const byId = id => document.getElementById(id);
  const escape = value => esc(value);
  const secure = url => safeUrl(url);
  const official = url => {const s = secure(url); return s === '#' ? '' : s;};
  const priceClass = o => o.within_budget === true ? 'price-good' :
    o.within_budget === false ? 'price-high' : 'price-neutral';
  const statusLabel = o => o.within_budget === true ? 'Within your budget' :
    o.within_budget === false ? 'Above your budget' :
    !o.total_is_complete ? 'Some prices unavailable' : 'Budget not specified';
  // ---- Needs picker: one row per need, identical in every view. -----------------------------
  // Every tool research found for a need is listed under it; the owner picks one tool per need
  // (or "I already have this" / "Skip"). Prices are always computed by the server.
  const openNeeds = new Set();          // rows the user expanded; restored on every redraw
  const researching = new Set();        // needs with a search in progress
  let picks = {}, picksPlan = null;     // need id -> tool id | 'have' | 'skip'
  let preview = null, previewKey = '', previewTimer = null, suggestion = null, lastDrawn = '';

  const needs = () => data?.components || [];
  const needById = id => needs().find(c => c.id === id);
  const toolsOf = need => need?.tools || [];
  const isConnector = n => n.capability === 'workflow_automation' && (n.connects || []).length >= 2;
  const nativeTool = n => {
    if (!isConnector(n)) return null;
    const chosen = n.connects.map(id => picks[id]);
    if (chosen.some(x => !x || x === 'have' || x === 'skip') || new Set(chosen).size !== 1) return null;
    return toolsOf(needById(n.connects[0])).find(t => t.tool_id === chosen[0]) || null;
  };
  const picksKey = () => 'bloom-picks:' + projectId;  // per project: a search's new plan version keeps your picks
  const searchKey = () => 'bloom-search:' + projectId;
  const seenKey = () => 'bloom-seen-tools:' + projectId;
  const readJSON = (key, fallback) => { try { return JSON.parse(sessionStorage.getItem(key)) ?? fallback; } catch { return fallback; } };
  const writeJSON = (key, value) => { try { sessionStorage.setItem(key, JSON.stringify(value)); } catch {} };
  const searching = () => ['queued','running'].includes(data?.run?.status) && !!data?.plan;
  const editable = () => !data?.approval && !!data?.plan &&
    (['waiting_approval','queued','running'].includes(data?.run?.status) || data?.run?.step === 'no_complete_option');
  const canResearch = () => !data?.approval && ['waiting_approval','completed'].includes(data?.run?.status);

  function picksFromOption(option) {
    const out = {};
    for (const a of option?.assignments || []) {
      if (a.tool_id && !['native','none'].includes(a.source)) out[a.component_id] = a.tool_id;
    }
    return out;
  }
  function loadPicks(plan) {
    if (picksPlan === plan.id) return;
    picksPlan = plan.id;
    const base = readJSON(picksKey(), null) || picksFromOption((plan.options || [])[0]);
    picks = {};
    for (const [id, choice] of Object.entries(base)) {
      const need = needById(id);
      if (need && (choice === 'have' || choice === 'skip' || toolsOf(need).some(t => t.tool_id === choice))) picks[id] = choice;
    }
    if (!readJSON(seenKey(), null)) markSeen();
  }
  function markSeen() {  // what the owner has already been shown; later finds get a "New" tag
    const seen = readJSON(seenKey(), {});
    for (const n of needs()) seen[n.id] = [...new Set([...(seen[n.id] || []), ...toolsOf(n).map(t => t.tool_id)])];
    writeJSON(seenKey(), seen);
  }

  const priceText = t => t.monthly_cost == null ? 'Price not listed' : t.monthly_cost === 0 ? 'Free' : money(t.monthly_cost) + '/mo';
  // "Reviewed" is about the listing (price and details checked), not about fit for this job.
  const trustTag = t => t.vetted ?
    '<span class="trust-label trust-reviewed" title="Our team checked this tool’s price and details. It says nothing about fit for your need.">Reviewed listing</span>' :
    '<span class="trust-label trust-check" title="Found by web research; confirm details on the vendor’s site.">Verify details</span>';
  const FIT = {built_for_this: ['fit-good', 'Built for this'], workable: ['fit-ok', 'Works, with limits'],
               poor: ['fit-poor', 'Not designed for this']};
  const fitBlock = t => FIT[t.fit] ? '<span class="tool-fit ' + FIT[t.fit][0] + '"><b>' + FIT[t.fit][1] + '</b>' +
    (t.fit_reason ? ' — ' + escape(t.fit_reason) : '') + '</span>' : '';
  const setupTag = t => ({'no-code':'No-code','low-code':'Low-code',developer:'Developer setup'})[t.setup_complexity] ?
    '<span class="need-tag' + (t.setup_complexity === 'developer' ? ' dev' : '') + '">' +
    ({'no-code':'No-code','low-code':'Low-code',developer:'Developer setup'})[t.setup_complexity] + '</span>' : '';

  function choiceLabel(need) {
    const native = nativeTool(need);
    if (native) return 'Built into ' + native.name;
    const c = picks[need.id];
    if (c === 'have') return 'Using what you already have';
    if (c === 'skip') return 'Skipped for now';
    const t = toolsOf(need).find(x => x.tool_id === c);
    if (!t) return 'Choose a tool';
    const caveat = t.fit === 'poor' ? ' · not designed for this' : t.fit === 'workable' ? ' · works, with limits' : '';
    return t.name + ' · ' + priceText(t) + caveat;
  }

  const shorten = (text, n) => !text ? '' : text.length > n ? text.slice(0, n - 1).trimEnd() + '…' : text;
  // One line on what a tool is good for: the comparison article's note, else its own description.
  const pitch = t => t.best_for ? 'Best for ' + t.best_for.replace(/^best for\s+/i, '').replace(/\.$/, '') : shorten(t.description, 130);

  function needRow(need, canEdit, seen) {
    const native = nativeTool(need);
    const isNew = t => !!seen[need.id] && !seen[need.id].includes(t.tool_id);
    const current = toolsOf(need).find(t => t.tool_id === picks[need.id]);
    // New finds first, so a finished search is easy to read.
    const tools = [...toolsOf(need)].sort((a, b) => isNew(b) - isNew(a));
    const choice = (value, inner) => '<label class="tool-choice' + (picks[need.id] === value ? ' selected' : '') + '">' +
      '<input type="radio" name="need-' + escape(need.id) + '" value="' + escape(value) + '" data-need="' +
      escape(need.id) + '"' + (picks[need.id] === value ? ' checked' : '') + (canEdit ? '' : ' disabled') + '>' + inner + '</label>';
    const rows = tools.map(t => {
      const also = (t.also_fits || []).map(id => needById(id)?.description).filter(Boolean);
      const site = official(t.site);
      const cheaper = current && current.tool_id !== t.tool_id && t.monthly_cost != null &&
        current.monthly_cost != null && t.monthly_cost < current.monthly_cost;
      return choice(t.tool_id, '<span class="tool-choice-main"><b>' + escape(t.name) + '</b>' +
        (isNew(t) ? ' <span class="need-tag new">New</span>' : '') +
        (cheaper ? ' <span class="need-tag cheaper">Cheaper than your pick</span>' : '') +
        '<span class="tool-choice-price">' + escape(priceText(t)) +
        (t.plan_name && t.monthly_cost != null ? ' · ' + escape(t.plan_name) : '') + '</span></span>' +
        fitBlock(t) + (pitch(t) ? '<span class="tool-choice-pitch">' + escape(pitch(t)) + '</span>' : '') +
        '<span class="tool-choice-meta">' + trustTag(t) + setupTag(t) +
        (also.length ? '<span>Also covers: ' + escape(also.join(', ')) + '</span>' : '') +
        (site ? '<a href="' + escape(site) + '" target="_blank" rel="noopener noreferrer">Visit website ↗</a>' : '') + '</span>');
    }).join('');
    const body = native ? '<p class="caption">Both steps happen in ' + escape(native.name) +
        ', so no separate connector is needed.</p>' :
      (tools.length ? rows : '<p class="caption">No tool found for this need yet.</p>') +
      choice('have', '<span class="tool-choice-main"><b>I already have this</b><span class="tool-choice-price">$0</span></span>') +
      choice('skip', '<span class="tool-choice-main"><b>Skip for now</b><span class="tool-choice-price">$0</span></span>');
    const share = suggestion && suggestion.from === need.id ? '<div class="share-suggest">' + escape(suggestion.name) +
      ' also covers ' + escape(suggestion.needs.map(id => needById(id)?.description).join(', ')) + '. ' +
      '<button type="button" class="research-btn" data-share="yes">Use ' + escape(suggestion.name) + ' there too</button>' +
      '<button type="button" class="link-btn" data-share="no">No thanks</button></div>' : '';
    return '<details class="need-pick' + (researching.has(need.id) ? ' is-searching' : '') + '" data-need-row="' +
      escape(need.id) + '"' + (openNeeds.has(need.id) ? ' open' : '') + '>' +
      '<summary><span class="need-name">' + escape(need.description) + '</span><span class="need-choice">' +
      escape(researching.has(need.id) ? 'Searching…' : choiceLabel(need)) + '</span><span class="need-count">' +
      (native ? '' : tools.length + (tools.length === 1 ? ' tool' : ' tools')) + '</span></summary>' +
      '<div class="need-body">' + searchBanner(need, tools.filter(isNew), current) + share + body +
      searchControl(need, canEdit, native) + '</div></details>';
  }

  // What happened with "Find cheaper or better options" for this need, in plain words.
  function searchBanner(need, found, current) {
    if (researching.has(need.id)) {
      return '<p class="research-status" role="status"><b>Searching the web for cheaper or better tools for this need.</b> ' +
        'This usually takes about a minute. You can keep choosing tools for your other needs meanwhile; ' +
        'new tools will appear here, marked “New”.</p>';
    }
    const last = readJSON(searchKey(), null);
    if (!last || last.need !== need.id || last.dismissed) return '';
    if (last.error) {
      return '<div class="research-result failed" role="status">The search didn’t finish: ' + escape(last.error) +
        ' <button type="button" class="link-btn" data-dismiss-search>Dismiss</button></div>';
    }
    if (!found.length) {
      return '<div class="research-result" role="status">Search finished: no new tools beyond the ones already listed. ' +
        'Your current choice still looks like a good fit. <button type="button" class="link-btn" data-dismiss-search>Dismiss</button></div>';
    }
    const cheaper = found.filter(t => t.monthly_cost != null && current?.monthly_cost != null && t.monthly_cost < current.monthly_cost);
    return '<div class="research-result" role="status"><b>Search finished: ' + found.length + ' new tool' +
      (found.length === 1 ? '' : 's') + ' found</b> — ' + escape(found.map(t => t.name + ' (' + priceText(t) + ')').join(', ')) +
      (cheaper.length ? '. Cheaper than your pick: ' + escape(cheaper.map(t => t.name).join(', ')) : '') +
      '. They are listed first below. <button type="button" class="link-btn" data-dismiss-search>Dismiss</button></div>';
  }

  function searchControl(need, canEdit, native) {
    if (!canEdit || native || researching.has(need.id)) return '';
    if (searching()) {
      return '<p class="caption">You can search for this need once the current search finishes (one search runs at a time).</p>';
    }
    return canResearch() ? '<button type="button" class="research-btn" data-research="' + escape(need.id) +
      '">Find cheaper or better options for this need ↻</button>' : '';
  }

  function summaryHtml(canEdit) {
    const o = preview;
    const missing = needs().filter(n => !nativeTool(n) && !picks[n.id]);
    const unpriced = o ? o.assignments.filter(a => a.monthly_cost == null &&
      !['skipped','existing','native','none'].includes(a.source)) : [];
    let message = '', canSave = false;
    if (missing.length) message = 'Choose a tool, “I already have this” or “Skip” for: ' + missing.map(n => n.description).join('; ');
    else if (!o) message = 'Pricing your selection…';
    else if (searching()) message = 'You can save once the search for “' + researchingNames() +
      '” finishes (about a minute), so the plan includes the latest tools.';
    else if (unpriced.length) message = [...new Set(unpriced.map(a => a.tool_name))].join(', ') +
      ' doesn’t list a price on its website, so this selection can’t be saved yet. Pick another tool or skip that need.';
    else canSave = true;
    const mismatched = needs().map(n => ({n, t: toolsOf(n).find(x => x.tool_id === picks[n.id])}))
      .filter(x => x.t && ['poor','workable'].includes(x.t.fit));
    if (canSave && mismatched.length) message = 'Before you save: ' + mismatched.map(x => x.t.name + ' is ' +
      (x.t.fit === 'poor' ? 'not designed for ' : 'usable, with limits, for ') + '“' + x.n.description.replace(/\.$/, '') + '”')
      .join('; ') + '. Open the row to see why and compare tools built for the job.';
    const quick = (data.plan.options || []).map(opt => '<button type="button" class="quick-pick" data-quick="' +
      escape(opt.id) + '">' + escape(opt.title) + '</button>').join('');
    return '<div class="selection-bar"><div><div class="mini-label">YOUR SELECTION</div><strong class="' +
      (o ? priceClass(o) : '') + '">' + (o && o.total_is_complete ? money(o.monthly_total) + '/month' : '—') + '</strong> ' +
      '<span class="caption">' + escape(o ? statusLabel(o) : '') + '</span><p class="caption" id="selection-message">' +
      escape(message) + '</p></div>' + (canEdit ? '<div class="selection-actions">' +
      (quick ? '<div class="quick-picks"><span class="caption">Quick picks:</span>' + quick + '</div>' : '') +
      '<button type="button" class="btn blue" id="save-selection"' + (canSave ? '' : ' disabled') +
      '>Save this plan →</button></div>' : '') + '</div>';
  }

  const researchingNames = () => [...researching].map(id => needById(id)?.description || 'this need').join('”, “');

  function drawNeeds() {
    const canEdit = editable();
    const seen = readJSON(seenKey(), {});
    byId('options').innerHTML = '<div class="needs-picker">' + needs().map(n => needRow(n, canEdit, seen)).join('') +
      '</div><div id="selection-summary">' + summaryHtml(canEdit) + '</div>';
  }
  function drawSummary() {
    const box = byId('selection-summary');
    if (box) box.innerHTML = summaryHtml(editable());
  }
  function picksChanged() {
    writeJSON(picksKey(), picks);
    drawNeeds();
    schedulePreview();
  }
  function schedulePreview() {
    const key = JSON.stringify(picks);
    if (key === previewKey && preview) return;
    previewKey = key; preview = null; drawSummary();
    clearTimeout(previewTimer);
    previewTimer = setTimeout(async () => {
      try {
        const r = await fetch('/api/projects/' + encodeURIComponent(projectId) + '/selection', {method:'POST',
          headers:{'Content-Type':'application/json'}, body: JSON.stringify({selections: picks})});
        const j = await r.json();
        if (r.ok && JSON.stringify(picks) === key) { preview = j.option; drawSummary(); }
      } catch {}
    }, 250);
  }

  async function researchMore(ids) {
    markSeen();  // so tools this search finds can be tagged "New"
    ids.forEach(id => researching.add(id));
    writeJSON(searchKey(), {need: ids[0], error: null, dismissed: false});
    drawNeeds();
    try {
      const r = await fetch('/api/projects/' + encodeURIComponent(projectId) + '/research', {method:'POST',
        headers:{'Content-Type':'application/json'}, body: JSON.stringify({component_ids: ids, goal: 'both'})});
      const j = await r.json();
      if (!r.ok) throw Error(j.error || 'Could not start the search');
      data.run.status = 'queued';
      render();
      setTimeout(poll, 600);
    } catch (err) {
      researching.clear();
      drawNeeds();
      byId('run-status').hidden = false;
      byId('run-status').textContent = err.message;
    }
  }
  root.bloomResearchMore = researchMore;

  async function saveSelection() {
    const button = byId('save-selection');
    button.disabled = true;
    try {
      const r = await fetch('/api/projects/' + encodeURIComponent(projectId) + '/approve', {method:'POST',
        headers:{'Content-Type':'application/json'}, body: JSON.stringify({plan_version: data.plan.version,
          option_id: 'custom', selections: picks, max_build_budget: Number(byId('build-budget')?.value) || 0,
          idempotency_key: 'sel-' + (crypto.randomUUID ? crypto.randomUUID() : Date.now() + '-' + Math.random())})});
      const j = await r.json();
      if (!r.ok && !(r.status === 409 && j.approval)) throw Error(j.error || 'Could not save this plan');
      setTimeout(poll, 300);
    } catch (err) {
      byId('selection-message').textContent = err.message;
      button.disabled = false;
    }
  }

  // Asked after research, only when a shown plan's price depends on the answer.
  function renderPricingQuestions(plan, show) {
    const box = byId('pricing-questions');
    const qs = (plan.pricing_questions || []);
    box.hidden = !qs.length || !show;
    if (box.hidden) { box.innerHTML = ''; return; }
    box.innerHTML = '<h3>Help us price this accurately</h3>' + qs.map(q =>
      '<label class="field">' + escape(q.question) + '<input type="number" min="0" step="1" data-pq="' +
      escape(q.field) + '" inputmode="numeric"></label>').join('') +
      '<button type="button" class="btn light" id="pricing-update">Update prices</button>' +
      '<p class="caption" id="pricing-status" aria-live="polite"></p>';
    byId('pricing-update').addEventListener('click', updatePricing);
  }

  async function updatePricing() {
    const body = {};
    byId('pricing-questions').querySelectorAll('[data-pq]').forEach(i => {
      if (i.value !== '') body[i.dataset.pq] = Number(i.value);
    });
    const status = byId('pricing-status');
    if (!Object.keys(body).length) { status.textContent = 'Enter at least one number first.'; return; }
    byId('pricing-update').disabled = true;
    status.textContent = 'Updating prices with your numbers…';
    try {
      const r = await fetch('/api/projects/' + encodeURIComponent(projectId) + '/revise', {method:'POST',
        headers:{'Content-Type':'application/json'}, body: JSON.stringify(body)});
      const j = await r.json();
      if (!r.ok) throw Error(j.error || 'Could not update prices');
      data.run.status = 'queued';
      setTimeout(poll, 600);
    } catch (err) {
      status.textContent = err.message;
      byId('pricing-update').disabled = false;
    }
  }

  function setResultView() {
    const hasNeeds = needs().length > 0 && !!data?.plan;
    byId('options').hidden = !hasNeeds;
    byId('adjust-toggle').hidden = !hasNeeds || !data?.recovery || !!data.approval;
    byId('selection-note').hidden = !hasNeeds || !!data.approval;
  }
  root.bloomSetResultView = setResultView;
  root.bloomRecoveryOpen = false;

  renderPlan = function () {
    const plan = data?.plan, approval = data?.approval;
    byId('plan-card').hidden = !plan;
    if (!plan) return;
    const serverSearching = data.run?.researching || [];
    if (searching()) {
      serverSearching.forEach(id => researching.add(id));
    } else if (researching.size) {
      const last = readJSON(searchKey(), {});
      writeJSON(searchKey(), {...last, error: data.run?.status === 'failed' ? (data.run.error || 'Please try again.') : null,
                              dismissed: false});
      researching.clear();
    }
    loadPicks(plan);
    byId('plan-title').textContent = approval ? 'Your saved plan' : 'Choose a tool for each need';
    byId('plan-meta').textContent = 'Budget: ' + (plan.monthly_budget == null ? 'not set' :
      money(plan.monthly_budget) + '/month') + ' · ' +
      (data.intake?.usage?.editors == null ? 'Confirm editing access' : (() => {
        const editors = data.intake.usage.editors;
        const viewers = Math.max(0,(data.intake.usage.seats || 1)-editors);
        return editors + (editors === 1 ? ' editor' : ' editors') + ', ' +
          viewers + (viewers === 1 ? ' viewer' : ' viewers');
      })());
    renderPricingQuestions(plan, data.run?.status === 'waiting_approval' && !approval);
    // Redraw only when something the owner sees has changed, so open rows and focus survive refreshes.
    const signature = JSON.stringify([plan.id, approval?.id, data.run?.status, data.run?.step, [...researching],
      needs().map(n => [n.id, toolsOf(n).map(t => t.tool_id + ':' + t.monthly_cost)])]);
    if (signature !== lastDrawn) {
      lastDrawn = signature;
      drawNeeds();
      schedulePreview();
    }
    byId('approve-line').hidden = !editable();
    byId('approved').hidden = !approval;
    if (approval) {
      const tools = (approval.recurring_cost.tools || []).filter(t => !['skipped','existing','native'].includes(t.choice))
        .map(t => t.tool);
      byId('approved-title').textContent = 'Saved to your plan: ' + ([...new Set(tools)].join(' + ') || 'no new tools');
      byId('approved-detail').textContent = 'Estimated recurring cost ' +
        money(approval.recurring_cost.monthly_total) + '/month · One-time setup budget ' +
        money(approval.max_build_budget) + '. You can now review the vendor websites and start your own setup.';
    }
    setResultView();
  };

  renderComponents = function () {
    const comps = needs();
    byId('needs-details').hidden = !comps.length;
    byId('needs-summary').textContent = 'What we understood · ' + comps.length +
      (comps.length === 1 ? ' task' : ' tasks');
    byId('components').innerHTML = comps.map(c =>
      '<div class="need-row"><span class="need-check" aria-hidden="true">✓</span><span>' +
        escape(c.description) + '</span></div>'
    ).join('');
    // Internal research diagnostics are not useful in the customer-facing checklist.
    byId('notes').hidden = true;
    byId('notes').replaceChildren();
  };

  const originalRender = render;
  render = function () {
    originalRender();
    const ready = !!data?.plan;
    const complete = ready && !!data.plan.options?.length;
    byId('consultation-card').classList.toggle('consultation-ready', ready);
    byId('steps').hidden = ready;
    byId('run-status').hidden = ready && complete && !searching();
    if (searching()) byId('run-status').textContent = 'Searching the web for cheaper or better tools… this usually ' +
      'takes about a minute. Your current picks stay below; new tools will be marked “New”.';
    if (ready) {
      byId('summary').textContent = data.approval ? 'Your plan is saved' :
        complete ? "Here's what your business needs" : 'We need a little more information to find a match';
    }
    // The detailed original request is already captured in the consultation.
    // Keep this screen focused on the specific needs we identified.
    byId('consult-goal').hidden = true;
    byId('consult-goal').textContent = '';
    if (!ready) byId('needs-details').open = true;
  };

  byId('adjust-toggle').addEventListener('click', () => {
    root.bloomRecoveryOpen = !root.bloomRecoveryOpen;
    if (typeof root.bloomRenderRecovery === 'function') root.bloomRenderRecovery();
    byId('adjust-toggle').textContent = root.bloomRecoveryOpen ?
      'Hide adjustments' : 'Adjust budget or team size';
    if (root.bloomRecoveryOpen) byId('recovery-details').open = true;
  });
  // Remember which rows are open so a redraw never collapses them.
  byId('options').addEventListener('toggle', e => {
    const row = e.target.closest?.('[data-need-row]');
    if (row) row.open ? openNeeds.add(row.dataset.needRow) : openNeeds.delete(row.dataset.needRow);
  }, true);
  byId('options').addEventListener('change', e => {
    const input = e.target.closest('input[data-need]');
    if (!input) return;
    const id = input.dataset.need, value = input.value;
    picks[id] = value;
    suggestion = null;
    const tool = toolsOf(needById(id)).find(t => t.tool_id === value);
    const others = (tool?.also_fits || []).filter(other => picks[other] !== value);
    if (others.length) suggestion = {from: id, tool: value, name: tool.name, needs: others};
    picksChanged();
  });
  byId('options').addEventListener('click', e => {
    const share = e.target.closest('[data-share]');
    if (share && suggestion) {
      if (share.dataset.share === 'yes') suggestion.needs.forEach(id => { picks[id] = suggestion.tool; });
      suggestion = null;
      picksChanged();
      return;
    }
    const quick = e.target.closest('[data-quick]');
    if (quick) {
      picks = picksFromOption((data.plan.options || []).find(o => o.id === quick.dataset.quick));
      suggestion = null;
      picksChanged();
      return;
    }
    if (e.target.closest('[data-dismiss-search]')) {
      writeJSON(searchKey(), {...readJSON(searchKey(), {}), dismissed: true});
      drawNeeds();
      return;
    }
    const more = e.target.closest('[data-research]');
    if (more) { openNeeds.add(more.dataset.research); researchMore([more.dataset.research]); return; }
    if (e.target.closest('#save-selection')) saveSelection();
  });
})(globalThis);
