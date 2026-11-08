"""Explainable recovery when a consultation has no fit or needs a budget trade-off.

Prices are calculated only from dated, vetted catalog entries. A product can be
shown as a *partial* match but is never presented as covering missing capabilities.
"""
from __future__ import annotations

from .pricing import plan_cost, plan_fits
from .schemas import Intake, Plan


def recovery_for(intake: Intake, components: list[dict], catalog: list[dict]) -> dict:
    needed = sorted({c.get("capability", "other") for c in components if c.get("capability") != "other"})
    has_other = any(c.get("capability") == "other" for c in components)
    plans = []
    for tool in catalog:
        if tool.get("status") != "vetted" or not tool.get("pricing_source_url") or not tool.get("pricing_checked_at"):
            continue
        covered = sorted(set(tool.get("capabilities", [])) & set(needed))
        if not covered:
            continue
        missing = sorted(set(needed) - set(covered))
        for raw in tool.get("plans", []):
            plan = Plan.model_validate(raw)
            if plan.monthly_price is None:
                continue  # unknown prices must never be presented as verified
            fits, _ = plan_fits(plan, intake.usage)
            if not fits:
                continue
            cost = plan_cost(plan, intake.usage)
            budget = intake.monthly_budget
            plans.append({
                "tool_id": tool["id"], "tool_name": tool["name"],
                "plan_name": plan.name, "monthly_price_per_unit": plan.monthly_price,
                "per_seat": plan.per_seat, "free_viewers": plan.free_viewers,
                "min_seats": plan.min_seats, "max_seats": plan.included.get("seats"),
                "billing": plan.billing, "monthly_cost": cost,
                "within_budget": None if budget is None else cost <= budget,
                "extra_per_month": None if budget is None else round(max(0, cost - budget), 2),
                "covered": covered, "missing": missing, "full_coverage": not missing and not has_other,
                "pricing_source_url": tool["pricing_source_url"],
                "pricing_checked_at": tool["pricing_checked_at"],
                "notes": tool.get("pricing_notes", ""),
            })
    plans.sort(key=lambda p: (not p["full_coverage"], p["monthly_cost"], p["tool_name"], p["plan_name"]))
    complete = [p for p in plans if p["full_coverage"]]
    within = [p for p in complete if p["within_budget"] is True]
    near = [p for p in complete if p["within_budget"] is False]
    unresolved = sorted({c.get("capability", "other") for c in components
                         if c.get("capability") == "other" or not any(
                             c.get("capability") in t.get("capabilities", [])
                             for t in catalog if t.get("status") == "vetted")})
    suggestions = []
    if has_other:
        suggestions.append("Clarify the task: describe what you create, track or approve, and where your data lives.")
    if unresolved and not has_other:
        suggestions.append("No reviewed product covers every requested task. Try a smaller first phase or a manual workflow.")
    if intake.usage.seats > 1 and intake.usage.editors is None and any(p["free_viewers"] for p in plans):
        suggestions.append(f"Confirm how many of your {intake.usage.seats} people need editing access; read-only access may be free.")
    if within:
        suggestions.append("At least one verified-price option is within budget. Confirm features and billing terms before choosing.")
    elif near:
        best = min(near, key=lambda p: p["extra_per_month"])
        suggestions.append(
            f"Closest full-coverage price: {best['tool_name']} {best['plan_name']} at "
            f"${best['monthly_cost']:g}/month, ${best['extra_per_month']:g} above your budget.")
    elif not complete and not unresolved:
        suggestions.append("No complete verified-price plan fits the current seat count. Try a different editor/viewer mix.")
    if intake.monthly_budget is None:
        suggestions.append("Confirm your monthly software budget to compare prices.")
    if not plans:
        suggestions.append("Try a spreadsheet or manual pilot while researching suitable software; no price can be verified here.")
    if not suggestions:
        suggestions.append("Compare a smaller pilot and verify feature limits before subscribing.")

    return {
        "status": ("unrecognized" if has_other else
                   "uncovered" if unresolved else
                   "within_budget" if within else
                   "over_budget" if near else "no_verified_price"),
        "budget": intake.monthly_budget,
        "total_people": intake.usage.seats,
        "editors": intake.usage.editors,
        "viewers": None if intake.usage.editors is None else intake.usage.seats - intake.usage.editors,
        "needed_capabilities": needed, "unresolved_capabilities": unresolved,
        "plans": plans[:50],
        "within_budget": within[:4],
        "near_misses": near[:4],
        "suggestions": suggestions[:4],
        "disclaimer": ("Prices are dated vendor catalog figures, not live quotes. Annual rates require "
                       "annual commitments; taxes, setup and add-ons are excluded. Feature fit "
                       "must be confirmed with the vendor."),
    }
