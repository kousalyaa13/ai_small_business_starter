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
from .budget import Limits, Meter
from .decompose import DECOMPOSE_INSTRUCTIONS, apply_answers, clean_decomposition, intake_prompt
from .options import build_options
from .research import Researcher, SearchClient
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
    # Research again until a part the catalog can't cover has at least this many known tools.
    min_alternatives: int = field(default_factory=lambda: int(os.environ.get("AGENT_MIN_ALTERNATIVES", 3)))


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
        return {"clarification_round": state.get("clarification_round", 0)}

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
            # Vetted catalog first, plus tools found by earlier research. Parts the catalog
            # can't cover are researched again whenever fewer than min_alternatives are known.
            vetted = catalog_mod.match(comp, intake, catalog)
            known = catalog_mod.match_discovered(comp, discovered, limit=None)
            found = vetted + [c for c in known if c.tool_id not in {v.tool_id for v in vetted}]
            if known:
                notes.append(f"Reused {len(known)} tool(s) found by earlier web research for '{comp.description}'.")
            candidates[comp.id] = [c.model_dump() for c in found]
            if not vetted and len(known) < deps.min_alternatives:
                gaps.append(comp.id)
        store.update_snapshot(state["run_id"], candidates=candidates, gaps=gaps, research_notes=notes)
        return {"candidates": candidates, "gaps": gaps, "research_notes": notes}

    def after_match(state):
        return "research_gaps" if state.get("gaps") else "build_options"

    def research_gaps(state):
        store.update_snapshot(state["run_id"], step="researching")
        intake = intake_of(state)
        researcher = Researcher(store, meter(state), deps.model, deps.search, deps.fetcher, deps.load_catalog())
        candidates, notes = dict(state["candidates"]), []
        comps = {c["id"]: Component.model_validate(c) for c in state["components"]}
        for cid in state["gaps"]:
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
                     ("research_gaps", research_gaps), ("build_options", build_options_node),
                     ("persist_plan", persist_plan), ("no_complete_option", no_complete_option),
                     ("await_approval", await_approval),
                     ("record_approval", record_approval)]:
        g.add_node(name, fn)
    g.add_edge(START, "validate_intake")
    g.add_edge("validate_intake", "decompose")
    g.add_conditional_edges("decompose", after_decompose, ["ask_clarification", "match_catalog"])
    g.add_edge("ask_clarification", "decompose")
    g.add_conditional_edges("match_catalog", after_match, ["research_gaps", "build_options"])
    g.add_edge("research_gaps", "build_options")
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
