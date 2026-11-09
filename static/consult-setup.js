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

  // ---- After saving: the setup plan ------------------------------------------------------------
  const originalRenderPlan = renderPlan;
  renderPlan = function () {
    originalRenderPlan();
    const check = byId('connection-check');
    if (check && data?.approval) check.hidden = true;
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

  const toolBox = s => '<span class="flow-box"><b>' + esc(s.tool) + '</b></span>';
  const ROUTE_WORD = {zapier: 'via Zapier', native: 'native integration', built_in: 'built in',
                      unverified: 'not verified', unchecked: 'not checked', manual: 'by hand'};

  // The mechanics of each connection: when it runs, what moves, and how.
  function flowHtml(wf, steps) {
    const rows = wf.edges.filter(e => e.kind !== 'manual').map(e => {
      const r = e.route || {};
      const detail = (label, text) => text ? '<div class="flow-detail"><span>' + label + '</span><p>' + esc(text) + '</p></div>' : '';
      return '<div class="flow-row"><div class="flow-line">' + toolBox(steps[e.from]) +
        '<span class="flow-arrow ' + (ROUTE_CLASS[r.route] || '') + '"><span>' + esc(ROUTE_WORD[r.route] || '') + '</span></span>' +
        toolBox(steps[e.to]) + '</div>' + detail('When', e.when) + detail('What moves', e.what_moves) + detail('How', e.how) +
        '<p class="caption flow-route">' + esc(r.label || '') + (r.note ? ' · ' + esc(r.note) : '') +
        (r.sources?.length ? ' · ' + r.sources.map((u, i) => link(u, 'source ' + (i + 1))).join(' ') : '') + '</p></div>';
    }).join('');
    const alone = wf.steps.filter(s => s.standalone).map(s => esc(s.tool));
    return '<div class="flow">' + (rows || '<p class="caption">None of your tools need to exchange data.</p>') +
      (alone.length ? '<p class="caption">' + alone.join(', ') + (alone.length === 1 ? ' runs' : ' run') +
        ' on its own; no data needs to move to or from ' + (alone.length === 1 ? 'it' : 'them') + '.</p>' : '') + '</div>';
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
