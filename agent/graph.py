"""LangGraph workflow: intake → components → catalog → web research → options → approval.

State holds identifiers and structured data only. Each node is safe to replay:
writes go through keyed store methods, and research results are cached per run.
"""
from __future__ import annotations

import operator
import os
import sqlite3
from dataclasses import dataclass, field
from typing import Annotated, Any, Callable, TypedDict

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from . import catalog as catalog_mod
from . import fit as fit_mod
from .budget import Limits, Meter
from .decompose import DECOMPOSE_INSTRUCTIONS, apply_answers, clean_decomposition, intake_prompt
from .options import build_options
from .research import Researcher, SearchClient, base_host
from .schemas import Candidate, Component, Decomposition, Intake, PlanVersion
from .store import now_iso

MAX_CLARIFICATION_ROUNDS = 2
class AgentState(TypedDict, total=False):
    project_id: str
    run_id: str
    requirement_version: int
    clarification_round: int
    summary: str
    components: list[dict]
    questions: list[dict]
    candidates: dict[str, list[dict]]
    gaps: list[str]
    research_notes: Annotated[list[str], operator.add]
    options: list[dict]
    plan_id: str
    plan_version: int
    approval_id: str
    force_research: list[str]  # part ids the user asked to search again
    research_goal: str  # "cheaper", "better" or "both"


@dataclass
class Deps:
    store: Any
    model: Any
    search: SearchClient | None
    fetcher: Any
    limits: Limits = field(default_factory=Limits)
    load_catalog: Callable[[], list[dict]] = catalog_mod.load_catalog
    # Discovered tools older than this are researched again instead of reused.
    discovered_max_age_days: int = field(default_factory=lambda: int(os.environ.get("AGENT_DISCOVERED_MAX_AGE_DAYS", 30)))
    # Skip web research for a need once this many known tools are rated "built for this" for it.
    min_good_tools: int = field(default_factory=lambda: int(os.environ.get("AGENT_MIN_GOOD_TOOLS", 2)))


def build_graph(deps: Deps, checkpointer):
    store = deps.store

    def meter(state):
        return Meter(store, state["run_id"], state["project_id"], deps.limits)

    def intake_of(state) -> Intake:
        _, data = store.latest_requirements(state["project_id"])
        return Intake.model_validate(data)

    def validate_intake(state):
        store.update_snapshot(state["run_id"], step="understanding")
        intake_of(state)  # raises if stored intake is invalid
        if state.get("components"):  # follow-up run: show the reused parts straight away
            store.update_snapshot(state["run_id"], summary=state.get("summary", ""), components=state["components"])
        return {"clarification_round": state.get("clarification_round", 0)}

    def after_validate(state):
        return "match_catalog" if state.get("components") else "decompose"

    def decompose(state):
        intake = intake_of(state)
        store.update_snapshot(state["run_id"], step="understanding")
        result = meter(state).model_call(deps.model, "decompose", DECOMPOSE_INSTRUCTIONS,
                                         intake_prompt(intake, state.get("clarification_round", 0)), Decomposition)
        components, questions = clean_decomposition(result, intake)
        if not components:
            components = [Component(id="c1", capability="other", description=intake.problem_text[:200],
                                    constraints=[], connects=[], search_query=intake.problem_text[:80])]
        comps = [c.model_dump() for c in components]
        store.update_snapshot(state["run_id"], summary=result.summary, components=comps)
        return {"summary": result.summary, "components": comps, "questions": [q.model_dump() for q in questions]}

    def after_decompose(state):
        if state.get("questions") and state.get("clarification_round", 0) < MAX_CLARIFICATION_ROUNDS:
            return "ask_clarification"
        return "match_catalog"

    def ask_clarification(state):
        store.update_snapshot(state["run_id"], step="clarifying", questions=state["questions"])
        answers = interrupt({"type": "clarification", "questions": state["questions"]})
        intake = apply_answers(intake_of(state), state["questions"], answers or {})
        version = store.add_requirement_version(state["project_id"], intake.model_dump(), state["requirement_version"])
        store.update_snapshot(state["run_id"], questions=[])
        return {"requirement_version": version, "questions": [],
                "clarification_round": state.get("clarification_round", 0) + 1}

    def match_catalog(state):
        store.update_snapshot(state["run_id"], step="checking_catalog")
        intake, catalog = intake_of(state), deps.load_catalog()
        discovered = store.discovered_tools(deps.discovered_max_age_days)
        candidates, gaps, notes = {}, [], []
        for comp in map(Component.model_validate, state["components"]):
            # Vetted catalog first, plus tools found by earlier research; which needs to research is
            # decided below, once the known tools' fit is rated.
            vetted = catalog_mod.match(comp, intake, catalog)
            known = catalog_mod.match_discovered(comp, discovered, limit=None)
            found = vetted + [c for c in known if c.tool_id not in {v.tool_id for v in vetted}]
            if known:
                notes.append(f"Reused {len(known)} tool(s) found by earlier web research for '{comp.description}'.")
            candidates[comp.id] = [c.model_dump() for c in found]
            forced = comp.id in state.get("force_research", [])
            if forced:
                gaps.append(comp.id)
        # Rate known tools' purpose first (one batched, cached call), then research only the needs that
        # known tools don't already handle well: research is the slowest part of a consultation.
        candidates, fit_notes = fit_mod.assess(meter(state), deps.model, store, intake, state["components"], candidates)
        notes.extend(fit_notes)
        for comp in state["components"]:
            if comp["id"] in gaps:
                continue
            cands = candidates.get(comp["id"], [])
            good = sum(1 for c in cands if c.get("fit") == "built_for_this")
            if not cands:
                gaps.append(comp["id"])
            elif comp["capability"] == "workflow_automation":
                continue  # general-purpose connectors are fine; one known connector is enough
            elif good < deps.min_good_tools:
                gaps.append(comp["id"])
                notes.append(f"Searching for tools made for '{comp['description']}': only {good} known tool(s) are built for it.")
        store.update_snapshot(state["run_id"], candidates=candidates, gaps=gaps, research_notes=notes)
        return {"candidates": candidates, "gaps": gaps, "research_notes": notes}

    def after_match(state):
        return "research_gaps" if state.get("gaps") else "assess_fit"

    def assess_fit_node(state):
        """Rate each tool's purpose against each need for this business type (cached; one batched call)."""
        store.update_snapshot(state["run_id"], step="building_options")
        candidates, notes = fit_mod.assess(meter(state), deps.model, store, intake_of(state),
                                           state["components"], {k: [dict(c) for c in v] for k, v in state["candidates"].items()})
        store.update_snapshot(state["run_id"], candidates=candidates)
        return {"candidates": candidates, "research_notes": notes}

    def research_gaps(state):
        store.update_snapshot(state["run_id"], step="researching")
        intake = intake_of(state)
        researcher = Researcher(store, meter(state), deps.model, deps.search, deps.fetcher, deps.load_catalog())
        candidates, notes = dict(state["candidates"]), []
        comps = {c["id"]: Component.model_validate(c) for c in state["components"]}
        forced, goal = set(state.get("force_research", [])), state.get("research_goal")
        for cid in state["gaps"]:
            current = candidates.get(cid, [])
            if cid in forced:
                # Look past what's already shown: alternatives to the current tools, cheaper and better ones.
                found, comp_notes = researcher.research(
                    comps[cid], intake, goal=goal or "both", current_names=[c["name"] for c in current],
                    exclude_hosts={base_host(c["site"]) for c in current if c.get("site")})
                new = [c for c in found if c.tool_id not in {x["tool_id"] for x in current}]
                comp_notes.append(f"Found {len(new)} new tool(s) for '{comps[cid].description}'." if new else
                                  f"No new tools found for '{comps[cid].description}' beyond those already shown.")
            else:
                found, comp_notes = researcher.research(comps[cid], intake)
            # Merge with tools already known for this part; a fresh look replaces the saved copy.
            fresh = {c.tool_id: c.model_dump() for c in found}
            kept = [c for c in candidates.get(cid, []) if c["tool_id"] not in fresh]
            candidates[cid] = [*fresh.values(), *kept]
            notes.extend(comp_notes)
        store.update_snapshot(state["run_id"], candidates=candidates,
                              research_notes=[*state.get("research_notes", []), *notes])
        return {"candidates": candidates, "research_notes": notes}

    def build_options_node(state):
        store.update_snapshot(state["run_id"], step="building_options")
        intake = intake_of(state)
        comps = [Component.model_validate(c) for c in state["components"]]
        cands = {cid: [Candidate.model_validate(c) for c in lst] for cid, lst in state["candidates"].items()}
        options = build_options(comps, cands, intake)
        return {"options": [o.model_dump() for o in options]}

    def persist_plan(state):
        intake = intake_of(state)
        gaps = [c["description"] for c in state["components"] if not state["candidates"].get(c["id"])]

        def payload(version):
            return PlanVersion(version=version, requirement_version=state["requirement_version"],
                               summary=state.get("summary", ""), monthly_budget=intake.monthly_budget,
                               usage=intake.usage, options=state["options"], gaps=gaps,
                               research_notes=state.get("research_notes", []), created_at=now_iso()).model_dump()

        plan = store.persist_plan(state["project_id"], state["run_id"], state["requirement_version"], payload)
        return {"plan_id": plan["id"], "plan_version": plan["version"]}

    def after_persist(state):
        return "await_approval" if state.get("options") else "no_complete_option"

    def no_complete_option(state):
        # Nothing to approve: every combination left at least one part without a tool.
        store.update_snapshot(state["run_id"], step="no_complete_option")
        return {}

    def await_approval(state):
        store.update_snapshot(state["run_id"], step="awaiting_approval")
        approval = store.approval_for_plan(state["plan_id"])
        if approval is None:
            interrupt({"type": "approval", "plan_id": state["plan_id"], "plan_version": state["plan_version"]})
            approval = store.approval_for_plan(state["plan_id"])
        if approval is None:
            raise RuntimeError("Resumed for approval but no approval is recorded.")
        return {"approval_id": approval["id"]}

    def record_approval(state):
        store.update_snapshot(state["run_id"], step="approved", approval_id=state["approval_id"])
        return {}

    g = StateGraph(AgentState)
    for name, fn in [("validate_intake", validate_intake), ("decompose", decompose),
                     ("ask_clarification", ask_clarification), ("match_catalog", match_catalog),
                     ("research_gaps", research_gaps), ("assess_fit", assess_fit_node),
                     ("build_options", build_options_node),
                     ("persist_plan", persist_plan), ("no_complete_option", no_complete_option),
                     ("await_approval", await_approval),
                     ("record_approval", record_approval)]:
        g.add_node(name, fn)
    g.add_edge(START, "validate_intake")
    g.add_conditional_edges("validate_intake", after_validate, ["decompose", "match_catalog"])
    g.add_conditional_edges("decompose", after_decompose, ["ask_clarification", "match_catalog"])
    g.add_edge("ask_clarification", "decompose")
    g.add_conditional_edges("match_catalog", after_match, ["research_gaps", "assess_fit"])
    g.add_edge("research_gaps", "assess_fit")
    g.add_edge("assess_fit", "build_options")
    g.add_edge("build_options", "persist_plan")
    g.add_conditional_edges("persist_plan", after_persist, ["await_approval", "no_complete_option"])
    g.add_edge("no_complete_option", END)
    g.add_edge("await_approval", "record_approval")
    g.add_edge("record_approval", END)
    return g.compile(checkpointer=checkpointer)


def sqlite_checkpointer(path):
    con = sqlite3.connect(str(path), check_same_thread=False, timeout=30)
    saver = SqliteSaver(con)
    saver.setup()
    return saver
