"""Recurring software cost calculations. Prices are never estimated by a model.

A plan's ``included`` limits map onto the customer's usage assumptions. Unknown
prices stay ``None`` and propagate as an incomplete total.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .schemas import Plan, Usage

# plan limit key -> Usage attribute it constrains
LIMIT_TO_USAGE = {"seats": "seats", "contacts": "contacts", "monthly_tasks": "monthly_volume"}


@dataclass
class PlanChoice:
    plan: Plan | None
    monthly_cost: float | None
    notes: list[str] = field(default_factory=list)


def billable_seats(plan: Plan, usage: Usage) -> int:
    """Only discount read-only users when the vendor explicitly verifies free viewers."""
    return usage.editors if plan.free_viewers and usage.editors is not None else usage.seats


def plan_fits(plan: Plan, usage: Usage) -> tuple[bool, list[str]]:
    """Return whether usage stays within the plan's stated limits, plus assumption notes."""
    notes = []
    if billable_seats(plan, usage) < plan.min_seats:
        return False, []
    for key, attr in LIMIT_TO_USAGE.items():
        limit = plan.included.get(key)
        if limit is None:
            continue
        if key == "seats" and plan.per_seat:
            continue  # per-seat plans scale with seats instead of capping them
        needed = billable_seats(plan, usage) if key == "seats" else getattr(usage, attr)
        if needed is None:
            notes.append(f"{plan.name} includes up to {limit:,} {key.replace('_', ' ')}; your volume was not provided.")
            continue
        if needed > limit:
            return False, []
    return True, notes


def plan_cost(plan: Plan, usage: Usage) -> float | None:
    if plan.monthly_price is None:
        return None
    price = float(plan.monthly_price)
    return round(price * billable_seats(plan, usage), 2) if plan.per_seat else round(price, 2)


def choose_plan(plans: list[Plan], usage: Usage) -> PlanChoice:
    """Pick the cheapest plan with a known price that fits usage.

    Falls back to a fitting plan with an unknown price, so the option is shown as
    incomplete rather than silently priced.
    """
    fitting = []
    for plan in plans:
        ok, notes = plan_fits(plan, usage)
        if ok:
            fitting.append((plan, notes))
    priced = [(p, n, plan_cost(p, usage)) for p, n in fitting if p.monthly_price is not None]
    if priced:
        plan, notes, cost = min(priced, key=lambda item: item[2])
        basis = "per seat" if plan.per_seat else "flat"
        paid = billable_seats(plan, usage)
        notes = [*notes, f"{plan.name}: ${plan.monthly_price:g}/month {basis}"
                 + (f" × {paid} paid editor(s)" if plan.per_seat else "")
                 + (f"; {usage.seats - paid} read-only viewer(s) free" if plan.free_viewers and paid < usage.seats else "")
                 + (" (annual billing commitment)" if plan.billing == "annual" else "")]
        if plan.free_viewers and usage.editors is None and usage.seats > 1:
            notes.append("Editor count not confirmed: price assumes all team members need paid editing access.")
        return PlanChoice(plan, cost, notes)
    if fitting:
        plan, notes = fitting[0]
        return PlanChoice(plan, None, [*notes, f"Price for {plan.name} could not be confirmed from the vendor's page."])
    if plans:
        return PlanChoice(None, None, ["No listed plan fits the usage you described; contact the vendor."])
    return PlanChoice(None, None, ["Pricing not found on the vendor's site."])


def option_total(costs_by_tool: dict[str, float | None], budget: float | None):
    """Sum each tool once (a tool covering several components is paid for once)."""
    known = [c for c in costs_by_tool.values() if c is not None]
    complete = len(known) == len(costs_by_tool)
    total = round(sum(known), 2) if known or complete else None
    if budget is None or total is None:
        within = None
    elif total > budget:
        within = False  # already over budget even with unknown prices excluded
    else:
        within = True if complete else None
    return total, complete, within
