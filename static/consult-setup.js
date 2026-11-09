/* After the owner chooses: how the tools work together, what to set up, and what it costs.
   Also checks, before saving, whether the picked tools can exchange data where they need to. */
(function (root) {
  const byId = id => document.getElementById(id);
  const ROUTE_CLASS = {built_in: 'route-good', native: 'route-good', zapier: 'route-good',
                       unverified: 'route-warn', unchecked: 'route-muted', manual: 'route-muted'};
  const link = (url, text) => {
    const safe = safeUrl(url);
    return safe && safe !== '#' ? '<a href="' + esc(safe) + '" target="_blank" rel="noopener noreferrer">' + esc(text) + '</a>' : '';
  };

  // ---- Before saving: can the picked tools connect? -------------------------------------------
  let checkTimer = null, checkKey = '';
  document.addEventListener('bloom:picks', e => {
    const box = byId('connection-check');
    if (!box) return;
    if (data?.approval || !data?.plan) { box.hidden = true; return; }
    const key = JSON.stringify(e.detail);
    if (key === checkKey) return;
    checkKey = key;
    clearTimeout(checkTimer);
    checkTimer = setTimeout(() => checkConnections(e.detail, key), 800);
  });

  async function checkConnections(picks, key) {
    const box = byId('connection-check');
    box.hidden = false;
    box.innerHTML = '<p class="caption">Checking how your picked tools connect…</p>';
    try {
      const r = await fetch('/api/projects/' + encodeURIComponent(projectId) + '/connections', {method: 'POST',
        headers: {'Content-Type': 'application/json'}, body: JSON.stringify({selections: picks})});
      const j = await r.json();
      if (key !== checkKey) return;  // the owner changed picks meanwhile
      const links = (j.connections || []).filter(c => c.route !== 'manual');
      if (!r.ok || !links.length) { box.hidden = true; return; }
      box.innerHTML = '<div class="mini-label">HOW YOUR TOOLS CONNECT</div><ul>' + links.map(c =>
        '<li class="' + (ROUTE_CLASS[c.route] || '') + '"><b>' + esc(c.from) + ' → ' + esc(c.to) + '</b>: ' + esc(c.label) +
        (c.sources?.length ? ' · ' + link(c.sources[0], 'source') : '') + '</li>').join('') + '</ul>' +
        (links.some(c => c.route === 'unverified') ? '<p class="caption">You can still save this plan; ' +
          'the setup steps will explain how to move the data by hand.</p>' : '');
    } catch {
      box.hidden = true;
    }
  }

  // ---- From the owner's picks to a setup guide --------------------------------------------------
  // Once every need has a choice (a tool, "I already have this" or "Skip"), the owner can build the setup
  // guide. That finalizes the selection on the server; the plan PDF download stays separate.
  const allChosen = picks => (data?.components || []).every(c =>
    picks[c.id] || (c.capability === 'workflow_automation' && (c.connects || []).length >= 2));
  function renderStart() {
    let box = byId('setup-start');
    if (!box) {
      box = document.createElement('section');
      box.id = 'setup-start';
      box.className = 'setup-start';
      (byId('connection-check') || byId('options')).insertAdjacentElement('afterend', box);
    }
    const current = typeof root.bloomPicks === 'function' ? root.bloomPicks() : {};
    const ready = !data?.approval && !!data?.plan && allChosen(current) &&
      (data.run?.status === 'waiting_approval' || data.run?.step === 'no_complete_option');
    box.hidden = !ready;
    if (!ready) return;
    box.innerHTML = '<div><b>Ready to set it up?</b><p class="caption">We’ll work out how your tools connect and write ' +
      'a step-by-step setup guide. This finalizes your current choices.</p></div>' +
      '<button type="button" class="btn blue" id="setup-start-btn">Build my setup guide →</button>' +
      '<p class="caption" id="setup-start-msg" role="status"></p>';
    byId('setup-start-btn').onclick = async () => {
      const button = byId('setup-start-btn'), msg = byId('setup-start-msg');
      button.disabled = true;
      msg.textContent = 'Starting…';
      try {
        const r = await fetch('/api/projects/' + encodeURIComponent(projectId) + '/approve', {method: 'POST',
          headers: {'Content-Type': 'application/json'}, body: JSON.stringify({plan_version: data.plan.version,
            option_id: 'custom', selections: current, max_build_budget: Number(byId('build-budget')?.value) || 0,
            idempotency_key: 'setup-' + (crypto.randomUUID ? crypto.randomUUID() : Date.now() + '-' + Math.random())})});
        const j = await r.json();
        if (!r.ok && !(r.status === 409 && j.approval)) throw Error(j.error || 'Could not start your setup guide');
        setTimeout(poll, 400);
      } catch (err) {
        msg.textContent = err.message;
        button.disabled = false;
      }
    };
  }
  // A changed pick may complete (or un-complete) the selection; re-check whether the guide can be started.
  document.addEventListener('bloom:picks', () => setTimeout(renderStart, 0));

  // ---- After saving: the setup plan ------------------------------------------------------------
  const originalRenderPlan = renderPlan;
  renderPlan = function () {
    originalRenderPlan();
    const check = byId('connection-check');
    if (check && data?.approval) check.hidden = true;
    renderStart();
    renderSetup();
  };

  let setupTimer = null;
  function renderSetup() {
    const box = byId('setup-plan');
    const setup = data?.setup;
    if (!box) return;
    if (!data?.approval || !setup) { box.hidden = true; return; }
    box.hidden = false;
    clearTimeout(setupTimer);
    if (setup.status === 'building') {
      box.innerHTML = '<span class="eyebrow">YOUR SETUP PLAN</span><h2>Building your setup plan…</h2>' +
        '<p class="caption">We’re checking how your tools connect and writing your setup steps. This usually takes under a minute.</p>';
      setupTimer = setTimeout(poll, 2500);  // keep refreshing until it's ready
      return;
    }
    if (setup.status === 'failed') {
      box.innerHTML = '<span class="eyebrow">YOUR SETUP PLAN</span><h2>We couldn’t build your setup plan</h2><p>' +
        esc(setup.error || 'Please try again.') + '</p><button type="button" class="btn blue" id="setup-retry">Try again</button>';
      byId('setup-retry').onclick = async () => {
        await fetch('/api/projects/' + encodeURIComponent(projectId) + '/setup/retry', {method: 'POST'});
        setTimeout(poll, 1200);
      };
      return;
    }
    const plan = setup.plan, wf = plan.workflow, done = new Set(setup.done || []);
    const steps = Object.fromEntries(wf.steps.map(s => [s.id, s]));
    box.innerHTML = '<span class="eyebrow">YOUR SETUP PLAN</span><h2>How your tools work together</h2>' +
      flowHtml(wf, steps) + guideHtml(plan.guide || [], done, plan.guide_error, plan.names_unconfirmed) +
      '<p class="caption">This guide is written for your plan; the apps themselves may label options differently. ' +
      'We don’t buy, connect or configure software for you.</p>';
    box.querySelectorAll('[data-step]').forEach(input => input.addEventListener('change', () => saveProgress(input)));
  }

  const ROUTE_WORD = {zapier: 'Automated through Zapier', native: 'Direct connection',
    built_in: 'Built into the app', unverified: 'Connection needs verification',
    unchecked: 'Connection not checked', manual: 'Manual transfer'};

  // Give owners the big picture first. Keep the implementation details available on demand.
  function flowHtml(wf, steps) {
    const edges = (wf.edges || []).filter(e => e.kind !== 'manual');
    const rows = edges.map(e => {
      const r = e.route || {};
      const viaZapier = r.route === 'zapier';
      const source = steps[e.from]?.tool || e.from;
      const target = steps[e.to]?.tool || e.to;
      const routeClass = ROUTE_CLASS[r.route] || 'route-muted';
      const node = (name, modifier) => '<span class="setup-flow-node ' + (modifier || '') + '">' +
        esc(name) + '</span>';
      const path = node(source, 'source') +
        '<span class="setup-flow-connector" aria-hidden="true">→</span>' +
        (viaZapier ? node('Zapier', 'connector') +
          '<span class="setup-flow-connector" aria-hidden="true">→</span>' : '') +
        node(target, 'target');
      const detail = (label, value) => value ? '<div class="setup-flow-detail"><b>' + label +
        '</b><p>' + esc(value) + '</p></div>' : '';
      const sources = (r.sources || []).map((url, i) => link(url, 'Source ' + (i + 1))).filter(Boolean).join(' · ');
      const description = e.what_moves || e.when || 'Connect these tools to keep information in sync.';
      return '<article class="setup-flow-card ' + routeClass + '">' +
        '<div class="setup-flow-path">' + path + '</div>' +
        '<div class="setup-flow-summary"><span class="setup-flow-method">' +
        esc(ROUTE_WORD[r.route] || 'Connection') + '</span><p>' + esc(description) + '</p></div>' +
        '<details class="setup-flow-more"><summary>How does this connection work?</summary>' +
        detail('When', e.when) + detail('Information shared', e.what_moves) +
        detail('How to connect', e.how) +
        (r.label || r.note ? '<p class="caption">' + esc([r.label, r.note].filter(Boolean).join(' · ')) + '</p>' : '') +
        (sources ? '<p class="caption">' + sources + '</p>' : '') + '</details></article>';
    }).join('');
    // Do not list a tool as standalone if it also participates in a connection.
    const connected = new Set(edges.flatMap(e => [e.from, e.to]));
    const alone = (wf.steps || []).filter(s => s.standalone && !connected.has(s.id))
      .map(s => s.tool).filter(Boolean);
    const uniqueAlone = [...new Set(alone)];
    return '<div class="setup-flow-overview">' +
      '<p class="caption setup-flow-intro">Here’s how your selected tools share information. ' +
      'Open a connection for the technical details, or follow the checklist below to set everything up.</p>' +
      (rows ? '<div class="setup-flow-grid">' + rows + '</div>' :
        '<p class="caption">No connections between your selected tools are required.</p>') +
      (uniqueAlone.length ? '<p class="caption setup-flow-independent"><b>Works independently:</b> ' +
        esc(uniqueAlone.join(', ')) + '. No connection needed for these tools.</p>' : '') + '</div>';
  }

  // One ordered guide across all tools: sign up, configure, connect, test, go live.
  function guideHtml(guide, done, error, namesUnconfirmed) {
    if (!guide.length) return error ? '<p class="err">' + esc(error) + '</p>' : '';
    const count = guide.filter(st => done.has(st.key)).length;
    return '<h3 class="setup-heading">Set it up, step by step <span class="caption">' + count + ' of ' + guide.length +
      ' done</span></h3>' + (namesUnconfirmed ? '<p class="caption">Some menu and option names below are our best ' +
        'description; the apps may label them a little differently.</p>' : '') + '<ol class="setup-guide">' + guide.map(st =>
        '<li class="guide-step' + (done.has(st.key) ? ' done' : '') + '"><label class="guide-title"><input type="checkbox" data-step="' +
        esc(st.key) + '"' + (done.has(st.key) ? ' checked' : '') + '> <b>' + esc(st.title) + '</b>' +
        (st.tool ? ' <span class="need-tag">' + esc(st.tool) + '</span>' : '') + '</label>' +
        '<ul>' + (st.substeps || []).map(t => '<li>' + esc(t) + '</li>').join('') + '</ul></li>').join('') + '</ol>';
  }

  async function saveProgress(input) {
    input.disabled = true;
    try {
      await fetch('/api/projects/' + encodeURIComponent(projectId) + '/setup/progress', {method: 'POST',
        headers: {'Content-Type': 'application/json'}, body: JSON.stringify({key: input.dataset.step, done: input.checked})});
      const set = new Set(data.setup.done || []);
      input.checked ? set.add(input.dataset.step) : set.delete(input.dataset.step);
      data.setup.done = [...set];
      const boxes = [...byId('setup-plan').querySelectorAll('[data-step]')];
      const heading = byId('setup-plan').querySelector('.setup-heading .caption');
      if (heading) heading.textContent = boxes.filter(b => b.checked).length + ' of ' + boxes.length + ' done';
      input.closest('.guide-step')?.classList.toggle('done', input.checked);
    } catch {
      input.checked = !input.checked;  // keep the box honest if saving failed
    } finally {
      input.disabled = false;
    }
  }
})(globalThis);
