/* Recovery UI: never invent prices or hide the reason for a missing match. */
(function () {
  const originalRenderPlan = renderPlan;
  renderPlan = function () {
    originalRenderPlan();
    renderRecovery();
  };

  // Prevent a silently contradictory intake (e.g. $75 in text and $50 in the field).
  $('intake-form').addEventListener('submit', event => {
    const f = new FormData(event.currentTarget);
    const text = String(f.get('problem_text') || '');
    const statedBudget = text.match(/\b(?:monthly\s+budget|budget\s+(?:of|is|:))\s*\$?([\d,]+(?:\.\d+)?)\s*(?:per\s+month|\/month|monthly|mo\b)/i);
    const typedBudget = f.get('monthly_budget');
    const statedSeats = text.match(/\b(\d{1,3})\s+(?:people|users|employees|staff|team members)\s+(?:need|will|who|to|have)\b/i);
    const typedSeats = f.get('seats');
    let message = '';
    if (statedBudget && typedBudget !== '' &&
        Math.abs(Number(statedBudget[1].replaceAll(',', '')) - Number(typedBudget)) > 0.001) {
      message = 'Your description says $' + statedBudget[1] + '/month, but the budget field says $' +
                typedBudget + '. Please make them match before continuing.';
    } else if (statedSeats && typedSeats !== '' && Number(statedSeats[1]) !== Number(typedSeats)) {
      message = 'Your description says ' + statedSeats[1] + ' people, but the team-size field says ' +
                typedSeats + '. Please confirm the correct number.';
    }
    if (message) {
      event.preventDefault();
      event.stopImmediatePropagation();
      $('intake-error').hidden = false;
      $('intake-error').textContent = message;
    }
  }, true);

  function renderRecovery() {
    const panel = $('recovery-panel');
    const plan = data && data.plan;
    const recovery = data?.recovery || {total_people: Number(data?.intake?.seats) || 1, editors: null, budget: data?.intake?.monthly_budget ?? null, suggestions: [], plans: [], disclaimer: ''};
    const approved = data && data.approval;
    const usable = !!(plan?.options || []).some(o => o.total_is_complete &&
      (o.assignments || []).every(a => a.source !== 'none'));
    const noneFound = !usable;
    panel.hidden = !plan || !!approved;
    panel.classList.toggle('has-matches', usable);
    const fitCard = $('fit-card');
    if (fitCard) fitCard.hidden = panel.hidden;
    if (panel.hidden) return;
    $('options').hidden = noneFound && !(data?.components || []).length;
    $('approve-line').hidden = noneFound || data.run?.status !== 'waiting_approval';

    // Never allow approving a placeholder gap or a plan with an unknown total.
    $('options').querySelectorAll('.option').forEach((el, index) => {
      const opt = plan.options[index];
      const blocked = !opt.total_is_complete || opt.assignments.some(a => a.source === 'none');
      el.classList.toggle('blocked', blocked);
      if (blocked) {
        el.querySelectorAll('[data-approve]').forEach(button => button.remove());
        if (!el.querySelector('.blocked-reason')) {
          const note = document.createElement('p');
          note.className = 'caption blocked-reason';
          note.textContent = 'Not ready to approve: this option has an unresolved tool or price.';
          el.appendChild(note);
        }
      }
    });

    $('recovery-title').textContent = noneFound ? "Let's find a better fit" : 'What could you change?';
    $('recovery-description').textContent = noneFound
      ? 'We could not verify a complete solution yet. Try these targeted changes instead of increasing your budget blindly.'
      : 'You have possible matches. Explore whether a different seat mix, billing term or budget unlocks a better option.';
    $('recovery-suggestions').innerHTML = (recovery.suggestions || [])
      .map(s => '<li>' + esc(s) + '</li>').join('');

    const total = Math.max(1, Number(recovery.total_people) || 1);
    const slider = $('editor-slider');
    slider.max = total;
    slider.value = recovery.editors == null ? Math.max(1, Math.ceil(total * 0.6)) : recovery.editors;
    $('recovery-budget').value = recovery.budget == null ? '' : recovery.budget;
    $('recovery-problem').value = data.intake?.problem_text || '';
    $('recovery-error').hidden = true;
    $('recovery-details').open = noneFound || !!globalThis.bloomRecoveryOpen;
    updatePriceExplorer();
  }

  function priceFor(plan, editors, total) {
    const paid = plan.free_viewers ? editors : total;
    if (paid < (plan.min_seats || 1) || (plan.max_seats != null && paid > plan.max_seats)) return null;
    return Number((plan.monthly_price_per_unit * (plan.per_seat ? paid : 1)).toFixed(2));
  }

  function updatePriceExplorer() {
    const recovery = data?.recovery || {total_people: Number(data?.intake?.seats) || 1, editors: null, plans: [], disclaimer: ''};
    const total = Math.max(1, Number(recovery.total_people) || 1);
    const editors = Math.max(1, Math.min(total, Number($('editor-slider').value) || total));
    const viewers = total - editors;
    const budgetRaw = $('recovery-budget').value;
    const budget = budgetRaw === '' ? null : Math.max(0, Number(budgetRaw) || 0);
    $('editor-count').textContent = editors + ' editor' + (editors === 1 ? '' : 's');
    $('viewer-count').textContent = viewers + ' view-only user' + (viewers === 1 ? '' : 's') +
      (recovery.editors == null ? ' · Example mix, not confirmed' : ' · Change this to compare');

    const priced = (recovery.plans || []).map(p => ({...p, estimate: priceFor(p, editors, total)}))
      .filter(p => p.estimate !== null);
    const full = priced.filter(p => p.full_coverage);
    const pool = full.length ? full : priced;
    // Keep one cheapest billing choice per product/tier, then show monthly alternatives in the same row.
    const groups = new Map();
    pool.forEach(p => {
      const tier = p.plan_name.replace(/\s*\((annual|monthly) billing\)\s*$/i, '');
      const key = p.tool_id + ':' + tier;
      const existing = groups.get(key) || [];
      existing.push(p);
      groups.set(key, existing);
    });
    const rows = [...groups.values()].map(g => g.sort((a,b) => a.estimate-b.estimate)[0]);
    rows.sort((a,b) => a.estimate-b.estimate);
    const under = budget == null ? rows : rows.filter(p => p.estimate <= budget);
    const over = budget == null ? [] : rows.filter(p => p.estimate > budget);
    over.sort((a,b) => (a.estimate-budget) - (b.estimate-budget));
    const shown = [...under.slice(0,3), ...over.slice(0,2)];
    const results = shown.length ? shown : rows.slice(0,4);
    const box = $('recovery-prices');
    if (!results.length) {
      box.innerHTML = '<div class="recovery-row"><div><b>No verified-price match yet</b><br><small>Clarify your task, or pilot a manual process while reviewing more vendors. Increasing the budget alone will not solve this.</small></div></div>';
      return;
    }
    box.innerHTML = results.map(p => {
      const delta = budget == null ? null : p.estimate - budget;
      const cls = delta == null || delta <= 0 ? 'ok' : 'over';
      const label = delta == null ? 'Set a budget to compare' :
        delta <= 0 ? money(-delta) + ' below your budget' :
          '+' + money(delta) + '/month (' + money(delta * 12) + '/year) to increase your budget';
      const tier = p.plan_name.replace(/\s*\((annual|monthly) billing\)\s*$/i, '');
      const group = groups.get(p.tool_id + ':' + tier) || [];
      const alternative = group.find(x => x.billing !== p.billing);
      const billing = p.billing === 'annual' ? 'Annual commitment' :
        p.billing === 'monthly' ? 'Monthly billing' : 'Free';
      const alternativeText = alternative ? ' · ' + money(alternative.estimate) + '/mo with ' +
        (alternative.billing === 'monthly' ? 'monthly billing' : 'annual commitment') : '';
      const missing = p.missing?.length ? ' · Partial match; does not cover: ' +
        p.missing.map(x => x.replaceAll('_',' ')).join(', ') : '';
      return '<div class="recovery-row"><div><b>' + esc(p.tool_name) + ' · ' + esc(tier) +
        '</b><br><small>' + esc(billing) + ' · ' + esc(p.per_seat ? editors + ' editors' : 'flat plan') +
        (p.free_viewers ? ', ' + viewers + ' free viewers' : '') + esc(alternativeText) +
        esc(missing) + '</small><br><a href="' + safeUrl(p.pricing_source_url) +
        '" target="_blank" rel="noopener noreferrer">Official pricing · checked ' +
        esc(p.pricing_checked_at) + ' ↗</a></div><div><div class="price ' + cls + '">' +
        money(p.estimate) + '<small>/mo</small></div><div class="delta ' + cls + '">' +
        esc(label) + '</div></div></div>';
    }).join('') +
    '<p class="caption" style="margin-top:8px">' + esc(recovery.disclaimer) +
    ' A priced product is not proof it meets all your fiscal-budget requirements.</p>';
  }

  globalThis.bloomRenderRecovery = renderRecovery;
  $('editor-slider').addEventListener('input', updatePriceExplorer);
  $('recovery-budget').addEventListener('input', updatePriceExplorer);
  $('recovery-apply').addEventListener('click', async () => {
    if (!data || !projectId) return;
    const btn = $('recovery-apply');
    btn.disabled = true;
    $('recovery-error').hidden = true;
    const body = {
      monthly_budget: $('recovery-budget').value === '' ? null : Number($('recovery-budget').value),
      editors: Number($('editor-slider').value),
      problem_text: $('recovery-problem').value.trim()
    };
    if (body.monthly_budget === null) delete body.monthly_budget;
    try {
      const r = await fetch('/api/projects/' + encodeURIComponent(projectId) + '/revise', {
        method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(body)
      });
      const j = await r.json();
      if (!r.ok) throw Error(j.error || 'Unable to revise this consultation.');
      $('plan-card').hidden = true;
      $('run-status').textContent = 'Updating your recommendation with the new budget and access requirements…';
      approveKey = null;
      setTimeout(poll, 350);
    } catch (err) {
      $('recovery-error').hidden = false;
      $('recovery-error').textContent = err.message;
    } finally {
      btn.disabled = false;
    }
  });
})();
