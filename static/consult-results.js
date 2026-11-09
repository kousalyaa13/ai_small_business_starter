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
  let activeNeed = null;                 // the need whose tools are shown in the carousel
  const trackScroll = {};                // carousel position per need, kept across redraws
  const researching = new Set();
  let comparisonSort = 'recommended';        // needs with a search in progress
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
  const savedKey = () => 'bloom-saved-plan:' + projectId;
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
    const saved = (() => { try { return JSON.parse(localStorage.getItem(savedKey())); } catch { return null; } })();
    const base = readJSON(picksKey(), null) || (saved?.planId === plan.id ? saved.selections : null) || picksFromOption((plan.options || [])[0]);
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
    '<span class="trust-label trust-check" title="Found by web research; confirm details on the vendor’s site.">Unverified</span>';
  const FIT = {built_for_this: ['fit-good', 'Built for this'], workable: ['fit-ok', 'Works, with limits'],
               poor: ['fit-poor', 'Not designed for this']};
  const fitBlock = t => FIT[t.fit] ? '<span class="tool-fit ' + FIT[t.fit][0] + '"><b>' + FIT[t.fit][1] + '</b>' +
    (t.fit_reason ? ' — ' + escape(t.fit_reason) : '') + '</span>' : '';
  const setupTag = t => ({'no-code':'No-code','low-code':'Low-code',developer:'Developer setup'})[t.setup_complexity] ?
    '<span class="need-tag' + (t.setup_complexity === 'developer' ? ' dev' : '') + '">' +
    ({'no-code':'No-code','low-code':'Low-code',developer:'Developer setup'})[t.setup_complexity] + '</span>' : '';

  const shorten = (text, n) => !text ? '' : text.length > n ? text.slice(0, n - 1).trimEnd() + '…' : text;
  // One line on what a tool is good for: the comparison article's note, else its own description.
  // What a tool is good for: the comparison article's note, else its own full description (never cut off in code).
  const pitch = t => t.best_for ? 'Best for ' + t.best_for.replace(/^best for\s+/i, '').replace(/\.$/, '') : (t.description || '');
  const expandedPitch = new Set();      // tools whose full description the owner opened
  const TOP_TOOLS = 5;                   // tools shown per need before "Show all"
  const allTools = new Set();            // needs where the owner chose "Show all"

  // ---- Horizontal layout: a strip of needs, and a carousel of tool cards for the open need. -----
  const pickedTool = need => toolsOf(need).find(t => t.tool_id === picks[need.id]);
  function needTabs(canEdit) {
    return '<div class="need-strip" role="tablist" aria-label="Your needs">' + needs().map((n, i) => {
      const t = pickedTool(n), native = nativeTool(n);
      const state = researching.has(n.id) ? 'Searching…' : native ? 'Built in' :
        picks[n.id] === 'have' ? 'Already have' : picks[n.id] === 'skip' ? 'Skipped' : t ? t.name : 'Choose a tool';
      // A chosen tool is just "done" here; fit details live on the tool cards, not as a warning in the strip.
      const status = (picks[n.id] || native) ? 'done' : 'todo';
      return '<button type="button" role="tab" class="need-tab ' + status + (n.id === activeNeed ? ' active' : '') +
        '" data-tab="' + escape(n.id) + '" aria-selected="' + (n.id === activeNeed) + '" aria-controls="need-panel">' +
        '<span class="need-tab-step">' + (i + 1) + '</span><span class="need-tab-text"><span class="need-tab-name">' +
        escape(shorten(n.description.replace(/\.$/, ''), 48)) + '</span><span class="need-tab-pick">' +
        (status === 'done' ? '✓ ' : '') + escape(state) + '</span></span></button>';
    }).join('') + '</div>';
  }

  // A compact, consistent feature comparison for the first three visible recommendations.
  // Only mark coverage when the tool's researched fit data supports it.
  const comparePages = {};
  function compareTools(need, tools, canEdit) {
    if (!tools.length) return '<p class="caption">No tools found for this need yet.</p>';
    const maxPage = Math.max(0, Math.ceil(tools.length / 3) - 1);
    const page = Math.min(comparePages[need.id] || 0, maxPage);
    comparePages[need.id] = page;
    const shown = tools.slice(page * 3, page * 3 + 3);
    const status = t => t.fit === 'built_for_this' ? '✓ Designed for this' :
      t.fit === 'workable' ? '◐ With limitations' :
      t.fit === 'poor' ? '— Not designed for this' : '— Not confirmed';
    const logo = t => {
      const url = official(t.site);
      const initial = '<span class="compare-initial">' + escape((t.name || '?').slice(0, 1)) + '</span>';
      if (!url) return initial;
      let icon = '';
      try { icon = new URL('/favicon.ico', url).href; } catch {}
      return '<span class="compare-logo"><img src="' + escape(icon) +
        '" alt="" loading="lazy" onerror="this.hidden=true;this.nextElementSibling.hidden=false">' +
        '<span class="compare-initial" hidden>' + escape((t.name || '?').slice(0, 1)) + '</span></span>';
    };
    const row = (label, fn, className = '') => '<tr class="' + className + '"><th scope="row">' + label + '</th>' +
      shown.map(t => '<td>' + fn(t) + '</td>').join('') + '</tr>';
    const fitDetail = t => {
      const fit = ({built_for_this:'Built for this',workable:'Works, with limits',poor:'Not designed for this'})[t.fit] || 'Fit not confirmed';
      return '<strong>' + fit + '</strong>' + (t.fit_reason ? ' — ' + escape(t.fit_reason) : '');
    };
    const select = t => {
      const selected = picks[need.id] === t.tool_id;
      return canEdit ? '<button type="button" class="btn ' + (selected ? 'light' : 'blue') +
        ' compare-pick" data-pick="' + escape(t.tool_id) + '" data-need="' + escape(need.id) +
        '" aria-pressed="' + selected + '">' + (selected ? 'Selected ✓' : 'Select') + '</button>' :
        (selected ? '<strong class="compare-yes">Selected ✓</strong>' : '—');
    };
    const pages = tools.length > 3 ? '<div class="compare-pagination"><span>' +
      (page * 3 + 1) + '–' + Math.min((page + 1) * 3, tools.length) + ' of ' + tools.length + ' tools</span>' +
      '<div><button type="button" class="nav-btn" data-compare-page="-1" ' + (page === 0 ? 'disabled' : '') +
      ' aria-label="Previous comparison tools">‹</button><button type="button" class="nav-btn" data-compare-page="1" ' +
      (page === maxPage ? 'disabled' : '') + ' aria-label="Next comparison tools">›</button></div></div>' : '';
    return '<div class="compare-wrap"><div class="compare-intro"><div><span class="mini-label">AT A GLANCE</span>' +
      '<h4>Compare your options</h4></div><span class="caption">Based on available vendor details; verify before purchasing.</span></div>' +
      '<div class="compare-scroll"><table class="compare-table"><thead><tr><th scope="col">Compare</th>' +
      shown.map(t => {
        const site = official(t.site);
        const name = '<span>' + escape(t.name) + '</span>';
        return '<th scope="col">' + logo(t) + (site ? '<a href="' + escape(site) +
          '" target="_blank" rel="noopener noreferrer" class="compare-site" aria-label="Open ' + escape(t.name) + ' website in a new tab">' + name + ' <span aria-hidden="true" class="external-link-arrow">↗</span></a>' : name) + '</th>';
      }).join('') + '</tr></thead><tbody>' +
      row('Monthly price', t => '<strong>' + escape(priceText(t)) + '</strong>' +
        (t.plan_name ? '<small class="compare-plan">' + escape(t.plan_name) + '</small>' : '')) +
      row('Best for', t => escape(pitch(t) || 'Not specified')) +
      row('Setup type', t => escape(({'no-code':'✓ No-code','low-code':'◐ Low-code',developer:'Developer setup'})[t.setup_complexity] || 'Not confirmed')) +
      row('Why this tool fits', fitDetail, 'compare-reason-row') +
      row('Your choice', select, 'compare-action-row') +
      '</tbody></table></div>' + pages + '</div>';
  }

  function toolCard(need, t, canEdit, isNew, current) {
    const selected = picks[need.id] === t.tool_id;
    const also = (t.also_fits || []).map(id => needById(id)?.description).filter(Boolean);
    const site = official(t.site);
    const cheaper = current && current.tool_id !== t.tool_id && t.monthly_cost != null &&
      current.monthly_cost != null && t.monthly_cost < current.monthly_cost;
    return '<article class="tool-card' + (selected ? ' selected' : '') + '" role="listitem">' +
      '<div class="tool-card-tags">' + (isNew ? '<span class="need-tag new">New</span>' : '') +
      (cheaper ? '<span class="need-tag cheaper">Cheaper than your pick</span>' : '') + '</div>' +
      fitBlock(t) +
      '<div class="tool-card-head"><b>' + escape(t.name) + '</b><span class="tool-card-price">' + escape(priceText(t)) +
      (t.plan_name && t.monthly_cost != null ? '<small>' + escape(t.plan_name) + '</small>' : '') + '</span></div>' +
      (pitch(t) ? '<p class="tool-card-pitch' + (expandedPitch.has(t.tool_id) ? ' open' : '') + '">' + escape(pitch(t)) + '</p>' +
        (pitch(t).length > 120 ? '<button type="button" class="link-btn more-btn" data-more="' + escape(t.tool_id) +
          '" aria-expanded="' + expandedPitch.has(t.tool_id) + '">' + (expandedPitch.has(t.tool_id) ? 'Less' : 'More') +
          '</button>' : '') : '') +
      '<div class="tool-choice-meta">' + trustTag(t) + setupTag(t) +
      (also.length ? '<span>Also covers: ' + escape(also.join(', ')) + '</span>' : '') + '</div>' +
      '<div class="tool-card-foot">' +
      (site ? '<a href="' + escape(site) + '" target="_blank" rel="noopener noreferrer">Visit website ↗</a>' : '<span></span>') +
      (canEdit ? '<button type="button" class="btn ' + (selected ? 'light' : 'blue') + ' pick-btn" data-pick="' +
        escape(t.tool_id) + '" data-need="' + escape(need.id) + '" aria-pressed="' + selected + '">' +
        (selected ? 'Selected ✓' : 'Select') + '</button>' : (selected ? '<span class="caption">Your pick</span>' : '')) +
      '</div></article>';
  }

  function needPanel(need, canEdit, seen) {
    const native = nativeTool(need);
    const isNew = t => !!seen[need.id] && !seen[need.id].includes(t.tool_id);
    const current = pickedTool(need);
    // New finds first, so a finished search is easy to read; then the server's fit-and-price order.
    const all = [...toolsOf(need)].sort((a, b) => {
      if (comparisonSort === 'lowest') {
        const costA = a.monthly_cost == null ? Infinity : a.monthly_cost;
        const costB = b.monthly_cost == null ? Infinity : b.monthly_cost;
        return costA - costB || isNew(b) - isNew(a);
      }
      if (comparisonSort === 'fit') {
        const rank = {built_for_this: 0, workable: 1, unknown: 2, poor: 3};
        return (rank[a.fit] ?? 2) - (rank[b.fit] ?? 2) || isNew(b) - isNew(a);
      }
      return isNew(b) - isNew(a);
    });
    // Top tools only, unless the owner asks for all; a picked tool is always kept in view.
    const showAll = allTools.has(need.id) || all.length <= TOP_TOOLS;
    const tools = showAll ? all : all.filter((t, i) => i < TOP_TOOLS || t.tool_id === picks[need.id]);
    const share = suggestion && suggestion.from === need.id ? '<div class="share-suggest">' + escape(suggestion.name) +
      ' also covers ' + escape(suggestion.needs.map(id => needById(id)?.description).join(', ')) + '. ' +
      '<button type="button" class="research-btn" data-share="yes">Use ' + escape(suggestion.name) + ' there too</button>' +
      '<button type="button" class="link-btn" data-share="no">No thanks</button></div>' : '';
    const index = needs().findIndex(n => n.id === need.id);
    const head = '<div class="need-panel-head"><div><div class="mini-label">NEED ' + (index + 1) + ' OF ' + needs().length +
      '</div><h3>' + escape(need.description) + '</h3>' +
      (need.plain_note ? '<p class="caption need-note">' + escape(need.plain_note) + '</p>' : '') + '</div>' +
      '<div class="need-nav"><button type="button" class="nav-btn" data-step="-1" aria-label="Previous need"' +
      (index === 0 ? ' disabled' : '') + '>‹ Previous need</button><button type="button" class="nav-btn" data-step="1" ' +
      'aria-label="Next need"' + (index === needs().length - 1 ? ' disabled' : '') + '>Next need ›</button></div></div>';
    if (native) {
      return head + '<p class="caption native-note">Both steps happen in ' + escape(native.name) +
        ', so no separate connector is needed.</p>';
    }
    const plain = (value, label) => canEdit ? '<button type="button" class="choice-chip' +
      (picks[need.id] === value ? ' selected' : '') + '" data-pick="' + value + '" data-need="' + escape(need.id) + '" aria-pressed="' +
      (picks[need.id] === value) + '">' + label + ' · $0</button>' : '';
    // "Show all" sits outside any one picker style, so it works with the comparison table.
    const showAllLink = all.length > TOP_TOOLS ? '<button type="button" class="link-btn show-all-btn" data-all="' +
      escape(need.id) + '">' + (showAll ? 'Show top ' + TOP_TOOLS + ' only' : 'Show all ' + all.length + ' tools') +
      '</button>' : '';
    const filters = '<div class="comparison-filters"><span class="caption">Sort tools:</span>' +
      [['recommended','Recommended'],['fit','Best fit'],['lowest','Lowest monthly cost']].map(([value,label]) =>
        '<button type="button" class="choice-chip' + (comparisonSort === value ? ' selected' : '') +
        '" data-sort="' + value + '" aria-pressed="' + (comparisonSort === value) + '">' + label + '</button>').join('') +
      (showAllLink ? '<span class="comparison-show-all">' + showAllLink + '</span>' : '') + '</div>';
    return head + searchBanner(need, tools.filter(isNew), current) + share + filters + compareTools(need, tools, canEdit) +
      '<div class="need-panel-foot">' + plain('have', 'I already have this') + plain('skip', 'Skip for now') +
      searchControl(need, canEdit, native) + '</div>';
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
    const names = found.map(t => t.name);
    const namesText = names.length === 1 ? names[0] : names.length === 2 ? names.join(' and ') : names.slice(0, -1).join(', ') + ', and ' + names[names.length - 1];
    const freeCount = found.filter(t => t.monthly_cost === 0).length;
    const freeNote = freeCount === found.length
      ? (found.length === 1 ? ' offers a free plan.' : ' both offer free plans.')
      : (freeCount ? ' includes ' + freeCount + ' option' + (freeCount === 1 ? '' : 's') + ' with a free plan.' : '.');
    return '<div class="research-result" role="status"><b>BloomBot found ' + found.length + ' more option' +
      (found.length === 1 ? '!' : 's!') + '</b> ' + escape(namesText) + escape(freeNote) +
      ' We’ve added ' + (found.length === 1 ? 'it' : 'them') + ' to your comparison so you can see how ' +
      (found.length === 1 ? 'it stacks' : 'they stack') + ' up against your current choice. ' +
      '<span class="caption">New options appear first.</span> <button type="button" class="link-btn" data-dismiss-search>Got it</button></div>';
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
    else canSave = true; // Unknown vendor prices do not prevent saving a draft.
    const knownAssignments = o?.assignments?.filter(a => a.monthly_cost != null &&
      !['skipped','existing','native','none'].includes(a.source)) || [];
    // Deduplicate products used across multiple needs; the server's total remains authoritative.
    const knownByTool = new Map();
    for (const a of knownAssignments) knownByTool.set(a.tool_id || a.tool_name, Number(a.monthly_cost));
    const knownSubtotal = [...knownByTool.values()].reduce((sum, value) => sum + value, 0);
    const totalText = !o ? 'Calculating…' : o.total_is_complete ?
      money(o.monthly_total) + '/month' : money(knownSubtotal) + '/month known';
    const unknownNote = o && !o.total_is_complete ? 'Estimated subtotal only — tools without verified prices are excluded.' : '';
    const saved = (() => {
      try { return JSON.parse(localStorage.getItem(savedKey())); }
      catch { return null; }
    })();
    const isSaved = !!saved && saved.planId === data?.plan?.id &&
      String(saved.planVersion) === String(data?.plan?.version) &&
      JSON.stringify(saved.selections) === JSON.stringify(picks);
    const quick = '';
    return '<div class="selection-bar"><div><div class="mini-label">YOUR SELECTION</div><strong class="' +
      (o ? priceClass(o) : '') + '">' + totalText + '</strong> ' +
      '<span class="caption">' + escape(o ? statusLabel(o) : '') + '</span><p class="caption" id="selection-message">' +
      escape(isSaved ? 'Plan saved in this browser. ' + (unknownNote || '') : (unknownNote && unpriced.length && !researching.size ? unknownNote : (unknownNote ? unknownNote + ' ' + message : message))) + '</p></div>' + (canEdit ? '<div class="selection-actions">' +
      (quick ? '<div class="quick-picks"><span class="caption">Quick picks:</span>' + quick + '</div>' : '') +
      '<button type="button" class="btn blue" id="save-selection"' + (canSave ? '' : ' disabled') +
      '>' + (isSaved ? 'Saved ✓' : 'Save this plan →') + '</button></div>' : '') + '</div>';
  }

  const researchingNames = () => [...researching].map(id => needById(id)?.description || 'this need').join('”, “');

  function drawNeeds() {
    const canEdit = editable();
    const seen = readJSON(seenKey(), {});
    if (!needById(activeNeed)) activeNeed = (needs().find(n => !picks[n.id] && !nativeTool(n)) || needs()[0])?.id || null;
    const need = needById(activeNeed);
    byId('options').innerHTML = '<div class="needs-picker">' + needTabs(canEdit) +
      '<section class="need-panel' + (researching.has(activeNeed) ? ' is-searching' : '') + '" id="need-panel" role="tabpanel">' +
      (need ? needPanel(need, canEdit, seen) : '') + '</section></div>';
    // The summary is a sticky sibling of the tools, so it remains visible as people compare.
    drawSummary();
    const track = byId('carousel-track');
    if (track) { track.scrollLeft = trackScroll[activeNeed] || 0; updateCount(); }
    byId('options').querySelector('.need-tab.active')?.scrollIntoView({block: 'nearest', inline: 'nearest'});
  }
  // "1–3 of 7": which cards are in view.
  function updateCount() {
    const track = byId('carousel-track'), label = byId('carousel-count');
    if (!track || !label) return;
    const cards = [...track.children];
    const box = track.getBoundingClientRect();
    const visible = cards.map((c, i) => [i, c.getBoundingClientRect()])
      .filter(([, r]) => r.right > box.left + 8 && r.left < box.right - 8).map(([i]) => i);
    label.textContent = visible.length && visible.length < cards.length ?
      (visible[0] + 1) + '–' + (visible[visible.length - 1] + 1) + ' of ' + cards.length + ' tools' :
      cards.length + (cards.length === 1 ? ' tool' : ' tools');
  }
  function scrollTrack(direction) {
    const track = byId('carousel-track');
    if (!track) return;
    const card = track.firstElementChild;
    const step = card ? card.getBoundingClientRect().width + 14 : track.clientWidth * 0.8;
    track.scrollBy({left: direction * step, behavior: 'smooth'});
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
    if (!button || !preview) return;
    button.disabled = true;
    const record = {
      planId: data.plan.id, planVersion: data.plan.version,
      savedAt: new Date().toISOString(), selections: {...picks},
      monthlyKnown: preview.monthly_total,
      totalIsComplete: !!preview.total_is_complete,
      assignments: preview.assignments.map(a => ({
        need: a.description, tool: a.tool_name, plan: a.plan_name,
        monthlyCost: a.monthly_cost, website: a.site_url || null
      }))
    };
    try {
      localStorage.setItem(savedKey(), JSON.stringify(record));
      drawSummary();
    } catch (err) {
      const message = byId('selection-message');
      if (message) message.textContent = 'Could not save in this browser. Check that site storage is enabled.';
      button.disabled = false;
    }
  }

  function setResultView() {
    const hasNeeds = needs().length > 0 && !!data?.plan;
    byId('options').hidden = !hasNeeds;
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

  function choose(id, value) {
    picks[id] = value;
    suggestion = null;
    const tool = toolsOf(needById(id)).find(t => t.tool_id === value);
    const others = (tool?.also_fits || []).filter(other => picks[other] !== value);
    if (others.length) suggestion = {from: id, tool: value, name: tool.name, needs: others};
    picksChanged();
  }
  function openNeed(id) {
    activeNeed = id;
    suggestion = null;
    drawNeeds();
  }
  byId('selection-summary').addEventListener('click', e => {
    if (e.target.closest('#save-selection')) saveSelection();
  });
  byId('options').addEventListener('scroll', e => {
    if (e.target.id !== 'carousel-track') return;
    trackScroll[activeNeed] = e.target.scrollLeft;
    updateCount();
  }, true);
  byId('options').addEventListener('keydown', e => {
    if (e.target.id === 'carousel-track' && ['ArrowLeft', 'ArrowRight'].includes(e.key)) {
      e.preventDefault();
      scrollTrack(e.key === 'ArrowRight' ? 1 : -1);
    }
  });
  byId('options').addEventListener('click', e => {
    const sort = e.target.closest('[data-sort]');
    if (sort) { comparisonSort = sort.dataset.sort; comparePages[activeNeed] = 0; drawNeeds(); return; }
    const tab = e.target.closest('[data-tab]');
    if (tab) { openNeed(tab.dataset.tab); return; }
    const step = e.target.closest('[data-step]');
    if (step) {
      const list = needs(), i = list.findIndex(n => n.id === activeNeed) + Number(step.dataset.step);
      if (list[i]) openNeed(list[i].id);
      return;
    }
    const showAll = e.target.closest('[data-all]');
    if (showAll) {
      const id = showAll.dataset.all;
      allTools.has(id) ? allTools.delete(id) : allTools.add(id);
      drawNeeds();
      return;
    }
    const toggle = e.target.closest('[data-more]');
    if (toggle) {
      const id = toggle.dataset.more;
      expandedPitch.has(id) ? expandedPitch.delete(id) : expandedPitch.add(id);
      const text = toggle.previousElementSibling;  // toggle in place so the carousel doesn't jump
      text.classList.toggle('open', expandedPitch.has(id));
      toggle.textContent = expandedPitch.has(id) ? 'Less' : 'More';
      toggle.setAttribute('aria-expanded', String(expandedPitch.has(id)));
      return;
    }
    const comparisonPage = e.target.closest('[data-compare-page]');
    if (comparisonPage) {
      const maxPage = Math.max(0, Math.ceil(toolsOf(needById(activeNeed)).length / 3) - 1);
      comparePages[activeNeed] = Math.max(0, Math.min(maxPage, (comparePages[activeNeed] || 0) + Number(comparisonPage.dataset.comparePage)));
      drawNeeds();
      return;
    }
    const arrow = e.target.closest('[data-scroll]');
    if (arrow) { scrollTrack(Number(arrow.dataset.scroll)); return; }
    const pick = e.target.closest('[data-pick]');
    if (pick) { choose(pick.dataset.need, pick.dataset.pick); return; }
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
    if (more) { researchMore([more.dataset.research]); return; }
    if (e.target.closest('#save-selection')) saveSelection();
  });
})(globalThis);
