"""How well each tool's intended purpose fits a need for this type of business.

Tools found online are rated while their pages are read. Catalog and previously saved tools
are rated here in one batched call per run, and every rating is cached by (tool, need,
business type) so the same combination is never rated twice.
"""
from __future__ import annotations

import hashlib

from .budget import BudgetExceeded
from .schemas import FitAssessments

FIT_RANK = {"built_for_this": 0, "workable": 1, "unknown": 1, "poor": 2}
MAX_PAIRS = 40

FIT_INSTRUCTIONS = """You judge how well software products fit a business need, based on what each product is made for.
For each pair, compare the product's intended purpose with the need and the type of business.
- "built_for_this": made for exactly this job in this kind of business
  (e.g. OpenTable or Resy for restaurant table reservations).
- "workable": can do the job but was made for something else or needs workarounds
  (e.g. Calendly, an appointment scheduler, for restaurant reservations; fine for pop-up events).
- "poor": would technically cover it but is a bad match for this business.
reason: one plain sentence (max 25 words) saying what the product is made for and what that means here.
Return one assessment per pair, echoing its key exactly."""


def fit_key(tool_id: str, need: str, business_type: str) -> str:
    raw = "|".join([tool_id, " ".join((need or "").lower().split()), (business_type or "").strip().lower()])
    return hashlib.sha1(raw.encode()).hexdigest()[:20]


def assess(meter, model, store, intake, components: list[dict], candidates: dict[str, list[dict]]):
    """Fill in fit for every candidate still unrated; returns (candidates, notes)."""
    comps = {c["id"]: c for c in components}
    pending = []
    for cid, cands in candidates.items():
        for cand in cands:
            if cand.get("fit") not in (None, "", "unknown"):
                continue
            key = fit_key(cand["tool_id"], comps[cid]["description"], intake.business_type)
            cached = store.get_fit(key)
            if cached:
                cand["fit"], cand["fit_reason"] = cached
            else:
                pending.append((key, comps[cid], cand))
    if not pending:
        return candidates, []
    batch = pending[:MAX_PAIRS]
    lines = [f"Business type: {intake.business_type or 'small business'}", f"Owner's goal: {intake.problem_text[:300]}", ""]
    # Short labels (p1, p2, ...) are echoed back reliably; long cache keys were not.
    for i, (_, comp, cand) in enumerate(batch, start=1):
        lines.append(f"[p{i}] Need: {comp['description']} | Product: {cand['name']} — "
                     f"{(cand.get('description') or '')[:200]} {('Best for ' + cand['best_for']) if cand.get('best_for') else ''}")
    try:
        result: FitAssessments = meter.model_call(model, "fit", FIT_INSTRUCTIONS, "\n".join(lines), FitAssessments)
    except BudgetExceeded:
        return candidates, ["Fit ratings were skipped because the run's limit was reached."]
    by_label = {a.key.strip().strip("[]").strip().lower(): a for a in result.assessments}
    for i, (key, _, cand) in enumerate(batch, start=1):
        rating = by_label.get(f"p{i}")
        if rating:
            cand["fit"], cand["fit_reason"] = rating.fit, rating.reason[:200]
            store.save_fit(key, rating.fit, cand["fit_reason"])
    return candidates, []
