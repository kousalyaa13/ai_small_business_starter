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
  const domain = url => {try {return new URL(official(url)).hostname.replace(/^www\./,'');} catch {return '';}};
  const productIcon = tool => {
    const host = domain(tool.site || tool.pricingUrl);
    const letter = (tool.name || '?').slice(0,1).toUpperCase();
    return '<span class="product-icon"><span class="product-initial">' + escape(letter) + '</span>' +
      (host ? '<img loading="lazy" alt="" referrerpolicy="no-referrer" src="https://www.google.com/s2/favicons?domain=' +
      encodeURIComponent(host) + '&sz=64">' : '') + '</span>';
  };
  const pricingLinks = (o, tools) => {
    const seen = new Set();
    const links = [];
    for (const t of tools) {
      const url = official(t.pricingUrl);
      if (!url || seen.has(url)) continue;
      seen.add(url);
      links.push('<a href="' + escape(url) + '" target="_blank" rel="noopener noreferrer">' +
        escape(t.name) + ' pricing' + (t.checkedAt ? ' · checked ' + escape(t.checkedAt) : '') + ' ↗</a>');
    }
    if (!links.length) {
      for (const u of o.sources || []) {
        const url = official(u);
        if (!url || seen.has(url)) continue;
        seen.add(url);
        links.push('<a href="' + escape(url) + '" target="_blank" rel="noopener noreferrer">' +
          escape(domain(url) || 'Source') + ' ↗</a>');
      }
    }
    return links.length ? links.join('') : '<span class="caption">No verified pricing link is available.</span>';
  };
  const toolLink = t => {
    const site = official(t.site || t.pricingUrl);
    return site ? '<a href="' + escape(site) + '" target="_blank" rel="noopener noreferrer" class="product-site">Visit website ↗</a>'
      : '<span class="caption">Website not available</span>';
  };
  const toolTitle = (t, compact=false) => '<div class="product-heading">' + productIcon(t) +
    '<div class="product-identity"><div class="' + (compact ? 'product-name compact' : 'product-name') + '">' +
    escape(t.name) + '</div><div class="product-plan">' + escape(t.plan || 'Plan not specified') +
    '</div></div></div>';
  const costLabel = o => o.monthly_total == null || !o.total_is_complete ?
    'Pricing needs verification' : money(o.monthly_total);
  const priceClass = o => o.within_budget === true ? 'price-good' :
    o.within_budget === false ? 'price-high' : 'price-neutral';
  const statusLabel = o => o.within_budget === true ? 'Within your budget' :
    o.within_budget === false ? 'Above your budget' :
    !o.total_is_complete ? 'Some prices unavailable' : 'Budget not specified';
  const commitment = tools => {
    const terms = [...new Set(tools.map(t => billingLabel(t.plan)).filter(Boolean))];
    return terms.length === 1 ? terms[0] : terms.length > 1 ? 'Mixed billing terms' : 'Confirm billing terms';
  };

  function cardDetails(o, tools) {
    const id = escape(o.id);
    const rows = tools.map(t =>
      '<div class="detail-tool"><b>' + escape(t.name) + '</b><p>' +
      escape(t.description || 'Confirm the product features with the vendor before purchasing.') +
      '</p></div>').join('');
    const costRows = tools.map(t => '<div class="detail-price-row"><span>' + escape(t.name) +
      ' · ' + escape(t.plan || 'Plan not specified') + '</span><b>' +
      (t.costUnknown ? 'Price not verified' : money(t.cost) + '/mo') + '</b></div>').join('');
    const tasks = uniqueTasks(o).map(t => '<li>' + escape(t.name) + '</li>').join('');
    const assumptions = (o.assumptions || []).map(a => '<li>' + escape(a) + '</li>').join('');
    const setup = (o.setup_requirements || []).map(a => '<li>' + escape(a) + '</li>').join('');
    return '<section class="product-details" id="detail-' + id + '" hidden>' +
      '<div class="detail-tabs" role="tablist" aria-label="Details for ' + escape(tools.map(t=>t.name).join(', ')) + '">' +
      ['overview','pricing','setup'].map((tab,i) => '<button type="button" role="tab" data-tab="' + tab +
        '" id="tab-' + id + '-' + tab + '" aria-controls="panel-' + id + '-' + tab +
        '" aria-selected="' + (i===0?'true':'false') + '" tabindex="' + (i===0?'0':'-1') +
        '">' + ({overview:'Overview',pricing:'Pricing details',setup:'Setup & limits'})[tab] + '</button>').join('') +
      '</div><div class="detail-panel" id="panel-' + id + '-overview" role="tabpanel" aria-labelledby="tab-' + id + '-overview" data-panel="overview"><h4>How this helps your business</h4>' +
      rows + '<h4>Tasks covered</h4><ul class="simple-list">' + tasks + '</ul>' +
      '<p class="detail-caution">Product features are based on catalog research and need confirmation for your specific plan.</p></div>' +
      '<div class="detail-panel" id="panel-' + id + '-pricing" role="tabpanel" aria-labelledby="tab-' + id + '-pricing" data-panel="pricing" hidden><h4>Cost breakdown</h4>' +
      costRows + '<div class="detail-price-row total-row"><span>Estimated monthly software cost</span><b>' +
      costLabel(o) + '</b></div><p class="detail-caution">Annual-plan figures are monthly equivalents, not month-to-month billing. Taxes, setup and optional add-ons are excluded.</p>' +
      '<div class="source-links">' + pricingLinks(o,tools) + '</div>' +
      (assumptions ? '<details class="extra-notes"><summary>Full pricing assumptions</summary><ul>' +
        assumptions + '</ul></details>' : '') + '</div>' +
      '<div class="detail-panel" id="panel-' + id + '-setup" role="tabpanel" aria-labelledby="tab-' + id + '-setup" data-panel="setup" hidden><h4>How to get started</h4>' +
      '<ol class="simple-list">' + setup + '</ol><p class="detail-caution">BloomBot does not buy, connect, or configure software for you. Review permissions, integrations, data handling, and features before subscribing.</p></div>' +
      '</section>';
  }

  function card(o, index, canChoose, approval) {
    const tools = groupTools(o);
    const tasks = uniqueTasks(o);
    const complete = o.total_is_complete && (o.assignments || []).every(a => a.source !== 'none');
    const first = tools[0];
    const single = tools.length === 1;
    const allVetted = tools.length > 0 && tools.every(t => t.vetted);
    const title = single ? first.name : tools.length + '-tool solution';
    const label = /lowest monthly cost/i.test(o.title) ? 'Lowest-cost option' :
      /alternative/i.test(o.title) ? 'Alternative' : o.title;
    const shortDesc = single && first.description ? first.description :
      o.summary || 'A combination of products selected for your needs.';
    const hero = single ? toolTitle(first) :
      '<div class="product-heading"><span class="product-icon bundle-icon">✦</span><div class="product-identity">' +
      '<div class="product-name">' + escape(title) + '</div><div class="product-plan">' +
      escape(o.title) + '</div></div></div>';
    const coverage = tasks.slice(0,3).map(t => '<li><span class="task-check">✓</span><span>' +
      escape(t.name) + '</span></li>').join('');
    const extra = tasks.length > 3 ? '<p class="caption">+ ' + (tasks.length-3) + ' more task(s)</p>' : '';
    const websites = single ? toolLink(first) :
      tools.map(t => '<span class="bundle-tool">' + productIcon(t) + '<span><b>' + escape(t.name) +
        '</b><small>' + escape(t.plan) + '</small></span>' + toolLink(t) + '</span>').join('');
    const selected = approval && approval.option_id === o.id;
    return '<article class="option result-card ' + (index === 0 ? 'featured ' : '') +
      (selected ? 'chosen ' : '') + '" data-option="' + escape(o.id) + '">' +
      '<div class="result-topline"><span class="result-label">' + escape(label) + '</span>' +
      '<span class="trust-label ' + (allVetted ? 'trust-reviewed' : 'trust-check') + '">' +
      (allVetted ? 'Reviewed catalog' : 'Verify this product') + '</span></div>' +
      hero + '<p class="result-description">' + escape(shortDesc) + '</p>' +
      '<div class="result-price ' + priceClass(o) + '"><strong>' + costLabel(o) +
      '</strong>' + (o.total_is_complete ? '<span>/month' +
        (tools.length > 0 && tools.every(t => billingLabel(t.plan) === 'Annual commitment') ? ' equivalent' : '') +
        '</span>' : '') + '</div>' +
      '<div class="result-billing">' + escape(commitment(tools)) +
      (o.total_is_complete && tools.every(t => billingLabel(t.plan) === 'Annual commitment') ?
        ' · ' + money(o.monthly_total * 12) + '/year' : '') + '</div>' +
      '<div class="result-budget ' + priceClass(o) + '">' + escape(statusLabel(o)) + '</div>' +
      '<div class="result-features"><div class="mini-label">COVERS YOUR NEEDS</div><ul>' +
      coverage + '</ul>' + extra + '</div>' +
      (!single ? '<div class="bundle-tools">' + websites + '</div>' : '') +
      '<div class="result-footer">' +
      (single ? websites : '') +
      '<button type="button" class="detail-toggle" data-detail="' + escape(o.id) +
      '" aria-expanded="false" aria-controls="detail-' + escape(o.id) + '">View details ↓</button></div>' +
      cardDetails(o,tools) +
      (canChoose && complete ? '<button type="button" class="btn blue choose-btn" data-approve="' +
        escape(o.id) + '">' + (single ? 'Choose this tool' : 'Choose this plan') + ' →</button>' :
        selected ? '<p class="selected-message">✓ Saved to your plan</p>' : '') +
      '</article>';
  }

  function compareTable(options, canChoose) {
    const tasks = new Map();
    options.forEach(o => uniqueTasks(o).forEach(t => tasks.set(t.id,t.name)));
    const columns = options.map(o => groupTools(o));
    const head = options.map((o,i) => '<th scope="col"><div class="compare-product">' +
      columns[i].map(t => '<div>' + productIcon(t) + '<span><b>' + escape(t.name) +
        '</b><small>' + escape(t.plan || 'Plan not specified') + '</small></span></div>').join('') +
      '</div></th>').join('');
    const row = (label, cells) => '<tr><th scope="row">' + escape(label) + '</th>' +
      cells.map(x => '<td>' + x + '</td>').join('') + '</tr>';
    const price = row('Monthly cost', options.map(o => '<b class="' + priceClass(o) + '">' +
      costLabel(o) + '</b>'));
    const billing = row('Billing', columns.map(tools => escape(commitment(tools))));
    const budget = row('Budget fit', options.map(o => '<span class="' + priceClass(o) + '">' +
      escape(statusLabel(o)) + '</span>'));
    const websites = row('Product website', columns.map(tools =>
      tools.map(t => toolLink(t)).join('<br>')));
    const featureRows = [...tasks].map(([id,name]) => row(name, options.map(o =>
      uniqueTasks(o).some(t => t.id===id && t.covered) ?
      '<span class="compare-yes" aria-label="Included">✓ Included</span>' :
      '<span class="compare-no">Not included</span>'))).join('');
    const actions = row('Next step', options.map(o => {
      const valid = o.total_is_complete && o.assignments.every(a => a.source !== 'none');
      return canChoose && valid ? '<button type="button" class="btn blue compare-choose" data-approve="' +
        escape(o.id) + '">Choose plan</button>' : '<span class="caption">Review plan</span>';
    }));
    return '<div class="compare-scroll"><table class="compare-table"><thead><tr><th scope="col">Compare</th>' +
      head + '</tr></thead><tbody>' + price + budget + billing + featureRows + websites + actions +
      '</tbody></table></div><p class="caption compare-footnote">Prices are estimates for the specified team. Verify current vendor terms, capabilities and plan limits before purchasing.</p>';
  }

  function setResultView() {
    const options = data?.plan?.options || [];
    const hasOptions = options.length > 0;
    const comparing = !!root.bloomCompareActive && options.length > 1;
    byId('options').hidden = !hasOptions || comparing;
    byId('compare-view').hidden = !hasOptions || !comparing;
    const button = byId('compare-toggle');
    button.hidden = options.length < 2;
    button.textContent = comparing ? '← Back to recommendations' : 'Compare tools ↔';
    byId('adjust-toggle').hidden = !hasOptions || !data?.recovery || !!data.approval;
    byId('selection-note').hidden = !hasOptions || !!data.approval;
  }
  root.bloomSetResultView = setResultView;
  root.bloomCompareActive = false;
  root.bloomRecoveryOpen = false;

  renderPlan = function () {
    const plan = data?.plan, approval = data?.approval;
    byId('plan-card').hidden = !plan;
    if (!plan) return;
    const canChoose = data.run?.status === 'waiting_approval' && !approval;
    const options = plan.options || [];
    byId('plan-title').textContent = options.length ? 'Find the right tool for your business' :
      'Let’s find a better fit';
    byId('plan-meta').textContent = 'Budget: ' + (plan.monthly_budget == null ? 'not set' :
      money(plan.monthly_budget) + '/month') + ' · ' +
      (data.intake?.usage?.editors == null ? 'Confirm editing access' : (() => {
        const editors = data.intake.usage.editors;
        const viewers = Math.max(0,(data.intake.usage.seats || 1)-editors);
        return editors + (editors === 1 ? ' editor' : ' editors') + ', ' +
          viewers + (viewers === 1 ? ' viewer' : ' viewers');
      })());
    byId('options').innerHTML = options.map((o,i) => card(o,i,canChoose,approval)).join('');
    byId('compare-view').innerHTML = options.length > 1 ? compareTable(options,canChoose) : '';
    byId('approve-line').hidden = !canChoose || !options.length;
    byId('approved').hidden = !approval;
    if (approval) {
      const o = options.find(x => x.id === approval.option_id);
      const tools = o ? groupTools(o).map(t=>t.name).join(' + ') : 'your chosen plan';
      byId('approved-title').textContent = 'Saved to your plan: ' + tools;
      byId('approved-detail').textContent = 'Estimated recurring cost ' +
        money(approval.recurring_cost.monthly_total) + '/month · One-time setup budget ' +
        money(approval.max_build_budget) + '. You can now review the vendor website and start your own setup.';
    }
    setResultView();
  };

  renderComponents = function () {
    const comps = data?.components || [];
    byId('needs-details').hidden = !comps.length;
    byId('needs-summary').textContent = 'What we understood · ' + comps.length +
      (comps.length === 1 ? ' task' : ' tasks');
    byId('components').innerHTML = comps.map(c => {
      const m = MATCH[c.match] || MATCH.pending;
      return '<div class="need-row"><span class="need-check">✓</span><span>' +
        escape(c.description) + '</span><span class="badge ' + m[0] + '">' +
        escape(m[1]) + '</span></div>';
    }).join('');
    const notes = data.research_notes || [];
    const needsAttention = !data.plan?.options?.length;
    byId('notes').hidden = !notes.length || !needsAttention;
    byId('notes').innerHTML = notes.map(n => '<li>' + escape(n) + '</li>').join('');
  };

  const originalRender = render;
  render = function () {
    originalRender();
    const ready = !!data?.plan;
    const complete = ready && !!data.plan.options?.length;
    byId('consultation-card').classList.toggle('consultation-ready', ready);
    byId('steps').hidden = ready;
    byId('run-status').hidden = ready && complete;
    if (ready) {
      byId('summary').textContent = complete ?
        (data.approval ? 'Your plan is saved' : 'Your recommendations are ready') :
        'We need a little more information to find a match';
    }
    byId('consult-goal').hidden = !ready;
    byId('consult-goal').textContent = data?.intake?.problem_text ?
      'Your goal: ' + data.intake.problem_text : '';
    byId('needs-details').open = !ready;
  };

  byId('compare-toggle').addEventListener('click', () => {
    root.bloomCompareActive = !root.bloomCompareActive;
    setResultView();
  });
  byId('adjust-toggle').addEventListener('click', () => {
    root.bloomRecoveryOpen = !root.bloomRecoveryOpen;
    if (typeof root.bloomRenderRecovery === 'function') root.bloomRenderRecovery();
    byId('adjust-toggle').textContent = root.bloomRecoveryOpen ?
      'Hide adjustments' : 'Adjust budget or team size';
    if (root.bloomRecoveryOpen) byId('recovery-details').open = true;
  });
  byId('options').addEventListener('click', e => {
    const details = e.target.closest('[data-detail]');
    if (details) {
      const box = byId('detail-' + details.dataset.detail);
      box.hidden = !box.hidden;
      details.setAttribute('aria-expanded', String(!box.hidden));
      details.textContent = box.hidden ? 'View details ↓' : 'Hide details ↑';
    }
    const tab = e.target.closest('[data-tab]');
    if (tab) {
      const box = tab.closest('.product-details');
      box.querySelectorAll('[data-tab]').forEach(b => {
        const active = b === tab;
        b.setAttribute('aria-selected', String(active));
        b.tabIndex = active ? 0 : -1;
      });
      box.querySelectorAll('[data-panel]').forEach(p => p.hidden = p.dataset.panel !== tab.dataset.tab);
    }
    const choose = e.target.closest('[data-approve]');
    if (choose && data?.plan) approve(choose.dataset.approve, data.plan.version);
  });
  byId('compare-view').addEventListener('click', e => {
    const choose = e.target.closest('[data-approve]');
    if (choose && data?.plan) approve(choose.dataset.approve, data.plan.version);
  });
  byId('options').addEventListener('keydown', e => {
    const tab = e.target.closest('[data-tab]');
    if (!tab || !['ArrowLeft','ArrowRight','Home','End'].includes(e.key)) return;
    const tabs = [...tab.closest('[role="tablist"]').querySelectorAll('[data-tab]')];
    const current = tabs.indexOf(tab);
    const next = e.key === 'Home' ? 0 : e.key === 'End' ? tabs.length - 1 :
      (current + (e.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length;
    e.preventDefault();
    tabs[next].click();
    tabs[next].focus();
  });
  byId('options').addEventListener('error', e => {
    if (e.target.tagName === 'IMG' && e.target.closest('.product-icon')) e.target.hidden = true;
  }, true);
  byId('compare-view').addEventListener('error', e => {
    if (e.target.tagName === 'IMG' && e.target.closest('.product-icon')) e.target.hidden = true;
  }, true);
})(globalThis);
