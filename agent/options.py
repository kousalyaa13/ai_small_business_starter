"""Turn matched candidates into two or three priced recommendation options."""
from __future__ import annotations

from .pricing import choose_plan, option_total
from .schemas import Assignment, Candidate, Component, Intake, Option

INF = float("inf")


def _covered_by_owned(c: Candidate) -> bool:
    return c.owned and c.owned_plan_covers


def _min_cost(c: Candidate, intake: Intake) -> float:
    if _covered_by_owned(c):
        return 0.0
    choice = choose_plan(c.plans, intake.usage)
    return INF if choice.monthly_cost is None else choice.monthly_cost


def _native(component: Component, picks: dict[str, Candidate | None]) -> Candidate | None:
    """A connector is unnecessary when every app it would connect is the same tool."""
    if component.capability != "workflow_automation" or len(component.connects) < 2:
        return None
    tools = {picks[cid].tool_id if picks.get(cid) else None for cid in component.connects}
    if len(tools) == 1 and None not in tools:
        return picks[component.connects[0]]
    return None


def _pick_cheapest(component, cands, intake, chosen_tools):
    if not cands:
        return None
    # Developer-only tools are shown as alternatives but are not picked while a no/low-code tool exists.
    return min(cands, key=lambda c: (c.setup_complexity == "developer", _min_cost(c, intake), not c.owned,
                                     c.tool_id not in chosen_tools, not c.vetted, c.name))


def strategy_lowest_cost(components, cands_by, intake, vetted_only=False):
    picks: dict[str, Candidate | None] = {}
    for comp in components:
        cands = [c for c in cands_by.get(comp.id, []) if c.vetted or not vetted_only]
        picks[comp.id] = _pick_cheapest(comp, cands, intake, {p.tool_id for p in picks.values() if p})
    return picks


def strategy_fewest_tools(components, cands_by, intake):
    remaining = [c for c in components if c.capability != "workflow_automation"]
    picks: dict[str, Candidate | None] = {}
    while remaining:
        tools: dict[str, tuple[Candidate, list[str]]] = {}
        for comp in remaining:
            for cand in cands_by.get(comp.id, []):
                tools.setdefault(cand.tool_id, (cand, []))[1].append(comp.id)
        if not tools:
            break
        cand, covered = max(tools.values(), key=lambda t: (t[0].setup_complexity != "developer", len(t[1]),
                                                           t[0].vetted, -_min_cost(t[0], intake)))
        for cid in covered:
            picks[cid] = cand
        remaining = [c for c in remaining if c.id not in covered]
    for comp in components:
        if comp.id not in picks:
            picks[comp.id] = _pick_cheapest(comp, cands_by.get(comp.id, []), intake,
                                            {p.tool_id for p in picks.values() if p})
    return picks


def alternatives_for(comp, chosen_id, cands_by, intake: Intake) -> list[dict]:
    """Every other tool that fits this part, cheapest first, so the customer sees the full field."""
    rows = []
    for c in cands_by.get(comp.id, []):
        if c.tool_id == chosen_id:
            continue
        if _covered_by_owned(c):
            plan, cost = "Your current plan", 0.0
        else:
            choice = choose_plan(c.plans, intake.usage)
            plan, cost = (choice.plan.name if choice.plan else None), choice.monthly_cost
        rows.append({"tool_id": c.tool_id, "name": c.name, "source": "owned" if _covered_by_owned(c) else c.source,
                     "vetted": c.vetted, "plan_name": plan, "monthly_cost": cost,
                     "site": c.site, "pricing_source_url": c.pricing_source_url,
                     "setup_complexity": c.setup_complexity, "best_for": c.best_for or c.audience})
    return sorted(rows, key=lambda r: (r["setup_complexity"] == "developer", r["monthly_cost"] is None,
                                       r["monthly_cost"] or 0, r["name"]))


def build_option(option_id, title, summary, components, picks, intake: Intake, cands_by=None) -> Option:
    usage = intake.usage
    tool_costs: dict[str, float | None] = {}
    tool_plan: dict[str, tuple[str | None, list[str]]] = {}
    assignments, assumptions, setup, sources, unvetted = [], [], [], [], []

    for comp in components:
        native = _native(comp, picks)
        cand = picks.get(comp.id)
        if native:
            assignments.append(Assignment(component_id=comp.id, capability=comp.capability, description=comp.description,
                                          tool_id=native.tool_id, tool_name=f"Built into {native.name}", source="native",
                                          vetted=native.vetted, monthly_cost=0.0,
                                          cost_basis="No separate connector needed: both steps happen in the same tool."))
            continue
        if cand is None:
            assignments.append(Assignment(component_id=comp.id, capability=comp.capability, description=comp.description,
                                          tool_id=None, tool_name="No suitable tool found", source="none", vetted=False,
                                          cost_basis="Not covered — needs a manual process or further research."))
            tool_costs[f"gap:{comp.id}"] = None
            continue
        first_use = cand.tool_id not in tool_costs
        if first_use:
            if _covered_by_owned(cand):
                tool_costs[cand.tool_id] = 0.0
                tool_plan[cand.tool_id] = ("Your current plan", [f"{cand.name}: you already use it; confirm your current plan includes this."])
            else:
                choice = choose_plan(cand.plans, usage)
                tool_costs[cand.tool_id] = choice.monthly_cost
                tool_plan[cand.tool_id] = (choice.plan.name if choice.plan else None, choice.notes)
                assumptions.extend(choice.notes)
                if cand.owned:
                    assumptions.append(f"You already use {cand.name}, but this capability needs the plan shown.")
            assumptions.extend(cand.notes)
            plan_name = tool_plan[cand.tool_id][0]
            setup.append(f"Create or upgrade your {cand.name} account" + (f" on the {plan_name} plan." if plan_name else "."))
            sources.extend(u for u in [cand.pricing_source_url, cand.site, *(e.url for e in cand.evidence)] if u)
            if not cand.vetted:
                unvetted.append(cand.name)
        plan_name, _ = tool_plan[cand.tool_id]
        cost = tool_costs[cand.tool_id] if first_use else 0.0
        if first_use:
            basis = tool_plan[cand.tool_id][1][-1] if tool_plan[cand.tool_id][1] else ""
        else:
            basis = f"Included in the {cand.name} cost above."
        assignments.append(Assignment(
            component_id=comp.id, capability=comp.capability, description=comp.description,
            tool_id=cand.tool_id, tool_name=cand.name,
            source="owned" if _covered_by_owned(cand) else cand.source, vetted=cand.vetted, plan_name=plan_name,
            monthly_cost=cost if tool_costs[cand.tool_id] is not None else None, cost_basis=basis,
            source_url=cand.pricing_source_url or cand.site, setup_complexity=cand.setup_complexity,
            best_for=cand.best_for or cand.audience,
            alternatives=alternatives_for(comp, cand.tool_id, cands_by or {}, intake)))

    total, complete, within = option_total(tool_costs, intake.monthly_budget)
    assumptions.insert(0, f"Assumes {usage.seats} user seat(s)"
                       + (f", {usage.contacts:,} contacts" if usage.contacts is not None else "")
                       + (f", {usage.monthly_volume:,} new records per month" if usage.monthly_volume is not None else "")
                       + ". Recurring software costs only; one-time setup is separate.")
    setup.append("You sign up and pay vendors directly; we never purchase on your behalf.")
    setup.append("Automatic setup by our platform is not available yet for this option.")
    return Option(id=option_id, title=title, summary=summary, assignments=assignments, monthly_total=total,
                  total_is_complete=complete, within_budget=within, assumptions=list(dict.fromkeys(assumptions)),
                  setup_requirements=list(dict.fromkeys(setup)), sources=list(dict.fromkeys(sources)),
                  unvetted_tools=unvetted)


def build_options(components: list[Component], cands_by: dict[str, list[Candidate]], intake: Intake) -> list[Option]:
    strategies = [
        ("Lowest monthly cost", "The cheapest combination that covers each part of your problem.",
         strategy_lowest_cost(components, cands_by, intake)),
        ("Fewest tools", "Covers as much as possible with one product, so there is less to set up and connect.",
         strategy_fewest_tools(components, cands_by, intake)),
        ("Vetted tools only", "Uses only tools from our reviewed catalog.",
         strategy_lowest_cost(components, cands_by, intake, vetted_only=True)),
    ]
    # When the strategies converge on one product, also offer the best combination without it.
    main_tools = [p.tool_id for p in strategies[0][2].values() if p]
    if main_tools:
        main = max(set(main_tools), key=main_tools.count)
        without = {cid: [c for c in lst if c.tool_id != main] for cid, lst in cands_by.items()}
        picks = strategy_lowest_cost(components, without, intake)
        if any(picks.values()):
            name = next(p.name for p in strategies[0][2].values() if p and p.tool_id == main)
            strategies.append((f"Alternative to {name}", f"The best combination that does not depend on {name}.", picks))
    options, seen = [], set()
    for title, summary, picks in strategies:
        signature = tuple((cid, p.tool_id if p else None) for cid, p in sorted(picks.items()))
        if signature in seen:
            continue
        seen.add(signature)
        option = build_option(f"opt{len(options) + 1}", title, summary, components, picks, intake, cands_by)
        # An option with a part left uncovered can't be acted on, so it is not offered.
        if any(a.source == "none" for a in option.assignments):
            continue
        options.append(option)
    # Options a small business can set up without developers first, then within budget, then cheapest.
    options.sort(key=lambda o: (any(a.setup_complexity == "developer" for a in o.assignments),
                                o.within_budget is not True, o.monthly_total if o.monthly_total is not None else INF))
    for i, opt in enumerate(options, start=1):
        opt.id = f"opt{i}"
    return options[:3]
