"""After approval: how the chosen tools work together, and a step-by-step guide to set them up.

The workflow and connection routes come from code and web checks. One model call explains each
connection (when it runs, what moves, how) and writes one ordered setup guide across all tools,
using the real integration pages it is given. Trigger and action names it uses must appear on
those pages; otherwise the step says the exact option names may differ.
"""
from __future__ import annotations

import re

from pydantic import BaseModel

from .budget import BudgetExceeded
from .connections import check_pair, route_for
from .research import quote_on_page
from .workflow import NO_TOOL, build_workflow

SETUP_INSTRUCTIONS = """You are setting up software for a small-business owner who has already chosen their tools.
Do not repeat what they chose, prices, plans or what each tool is; they know. Explain how the tools work together
and give them an ordered, practical setup guide.

For each connection [eN] write:
- when: the event that starts it, in plain words (e.g. "A visitor submits the contact form").
- what_moves: the data that moves (e.g. "Name, email, phone and message").
- how: the concrete mechanism for the given route. For "via Zapier", describe the Zap (trigger app and event, any
  search/dedupe step, action app and event). For "native integration", where in the product to switch it on.
  For "built in", what happens inside the one tool. For "not verified", how to move the data by hand (export/import).

steps: one guide across all tools, in the order things must be done:
1) sign up for and configure each tool that data starts in, 2) each tool data goes to, 3) set up each connection,
4) send one test record end to end and check it arrived once, 5) go live. 6 to 12 steps.
Each step: a short title, tool = the [sN] label it is about ("" if it covers several), and 2 to 5 concrete
substeps an owner can follow. Build in the owner's requirements (e.g. matching on email to avoid duplicates).
If a tool's price is unknown, add a substep to confirm it with the vendor before subscribing.
Use exact trigger, action or menu names only if they appear in the integration details given; otherwise
describe them generically. List every exact name you used in references, copied exactly.
Echo each label exactly."""


class FlowDetail(BaseModel):
    label: str
    when: str
    what_moves: str
    how: str


class GuideStep(BaseModel):
    title: str
    tool: str
    substeps: list[str]
    references: list[str]


class SetupGuide(BaseModel):
    flows: list[FlowDetail]
    steps: list[GuideStep]


def _guide_prompt(intake, components, tools, edges, all_steps):
    constraints = {c["id"]: c.get("constraints") or [] for c in components}
    lines = [f"Business type: {intake.business_type or 'small business'}", f"Owner's goal: {intake.problem_text[:300]}", ""]
    step_labels, edge_labels = {}, {}
    for i, s in enumerate(tools, start=1):
        step_labels[f"s{i}"] = s["id"]
        price = "" if s["monthly_cost"] is not None else " | price unknown: confirm with the vendor"
        reqs = "; ".join(constraints.get(s["id"], []))
        lines.append(f"[s{i}] {s['tool']} — used for: {s['need']}" + (f" | requirements: {reqs}" if reqs else "") + price)
    # Look ends up among all steps: an arrow can start at a need marked "already have" or "skip".
    by_id = {s["id"]: s for s in all_steps}
    for i, e in enumerate([e for e in edges if e["kind"] != "manual"], start=1):
        edge_labels[f"e{i}"] = e["id"]
        reqs = "; ".join(constraints.get(e["id"], []))
        lines.append(f"[e{i}] From {by_id[e['from']]['tool']} to {by_id[e['to']]['tool']} | route: {e['route']['label']}"
                     + (f" | requirements: {reqs}" if reqs else ""))
        details = (e.get("details") or {}).get("text")
        if details:
            lines.append(f"    Integration details for [e{i}] (from {e['details']['url']}): {details}")
    return "\n".join(lines), step_labels, edge_labels


def _label(text, prefix):
    """'e1', '[e1]', 'E1 (HubSpot → Jobber)' -> 'e1': models don't always echo labels exactly."""
    m = re.search(rf"\b{prefix}\d+\b", (text or "").lower())
    return m.group(0) if m else ""


def build_setup_plan(meter, model, store, search, fetcher, intake, components, option, max_build_budget):
    workflow = build_workflow(components, option)
    steps = {s["id"]: s for s in workflow["steps"]}
    for edge in workflow["edges"]:
        src, dst = steps[edge["from"]], steps[edge["to"]]
        check = check_pair(store, search, fetcher, src, dst) if edge["kind"] == "connector" else None
        edge["route"] = route_for(edge, src, dst, check)
        edge["details"] = (check or {}).get("details")
    tools = [s for s in workflow["steps"] if s["choice"] not in NO_TOOL]
    guide, error, unconfirmed_names = [], None, False
    if tools:
        prompt, step_labels, edge_labels = _guide_prompt(intake, components, tools, workflow["edges"], workflow["steps"])
        try:
            result: SetupGuide = meter.model_call(model, "setup", SETUP_INSTRUCTIONS, prompt, SetupGuide)
            flows = {edge_labels.get(_label(f.label, "e")): f for f in result.flows}
            for edge in workflow["edges"]:
                f = flows.get(edge["id"])
                edge.update(when=f.when, what_moves=f.what_moves, how=f.how) if f else None
            evidence = " ".join((e.get("details") or {}).get("text", "") for e in workflow["edges"])
            names = {s["id"]: s["tool"] for s in tools}
            for i, st in enumerate(result.steps[:12]):
                tool_id = step_labels.get(_label(st.tool, "s"))
                # Integration-specific names must appear on a real integration page; if some don't,
                # one note above the guide says so (a note on every step was noise).
                if any(r.strip() and not quote_on_page(r, evidence) for r in st.references):
                    unconfirmed_names = True
                guide.append({"key": f"step:{i}", "title": st.title, "tool": names.get(tool_id, ""),
                              "substeps": st.substeps[:5]})
        except BudgetExceeded:
            error = "The setup guide could not be written because the run's limit was reached."
    for edge in workflow["edges"]:
        edge.pop("details", None)  # page excerpts were for the guide writer, not the owner
    return {"workflow": workflow, "guide": guide, "guide_error": error,
            "names_unconfirmed": unconfirmed_names}
