"""Problem decomposition: the instructions, prompt and clean-up for breaking a business
problem into capability-tagged components, plus applying clarifying answers.

Kept free of graph, storage and research dependencies so it can be run and tested alone.
"""
from __future__ import annotations

from .capabilities import CAPABILITIES
from .schemas import ClarifyingQuestion, Component, Decomposition, Intake

MAX_COMPONENTS = 6
MAX_CONSTRAINTS = 3

DECOMPOSE_INSTRUCTIONS = """You are a technical consultant for small businesses.
Break the owner's problem into the smallest set of components that existing software products can handle.
Rules:
- Each component gets exactly one capability tag from the allowed list. The tag must describe the
  component's main job, the thing a typical product with that tag does on its own. If the main job is
  something else that merely involves a tagged task (e.g. answering phone calls and also booking jobs),
  use "other" for that main job; add a separate tagged component only if a separate tool is needed.
  Example: an AI phone receptionist that books appointments is "other", not "appointment_booking".
- Only include components the owner's outcome needs. Do not add extras they did not ask for
  (e.g. separate file storage, backups, analytics or reporting) unless the outcome cannot work without them.
- Add a "workflow_automation" component only when data must move between two different components;
  list the ids of those components in "connects".
- search_query: 3 to 8 plain words a business owner would type into a search engine to find tools
  for this component, including the business type when known (e.g. "AI receptionist for plumbers",
  "online booking app for hair salons"). No jargon, no budget, no punctuation.
- description: plain language a business owner understands, 15 words or fewer.
- constraints: at most 3 per component. Include only requirements the owner stated or that are
  strictly necessary for the component to work. Do not invent features, quantities or extras.
- Never put budget, price, seats or volume in constraints; the application handles those separately.
- For duplicates, match on email unless the owner names a different identifier.
- Ask a clarifying question only if the answer would change which tools fit (budget, volume, seats, paid editors,
  where existing data lives). Do not ask about anything already provided. At most 2 questions.
- summary: one sentence, 25 words or fewer.
- Use ids c1, c2, ... and q1, q2, ...
Allowed capability tags:
""" + "\n".join(f"- {k}: {v}" for k, v in CAPABILITIES.items())


def intake_prompt(intake: Intake, round_no: int) -> str:
    u = intake.usage
    lines = [
        f"Problem: {intake.problem_text}",
        f"Business: {intake.business_name or 'not provided'}",
        f"Business type: {intake.business_type or 'not provided'}",
        f"Existing software: {', '.join(intake.existing_software) or 'none listed'}",
        f"Monthly budget: {'not provided' if intake.monthly_budget is None else f'${intake.monthly_budget:g}'}",
        f"Total people needing access: {u.seats}",
        f"Paid editors: {u.editors if u.editors is not None else 'not confirmed (assume all people edit until clarified)'}",
        f"Contacts: {'not provided' if u.contacts is None else u.contacts}",
        f"Monthly volume: {'not provided' if u.monthly_volume is None else u.monthly_volume}",
        f"Requirements: {'; '.join(intake.requirements) or 'none'}",
        f"Clarification rounds so far: {round_no}",
    ]
    return "\n".join(lines)


def clean_decomposition(result: Decomposition, intake: Intake) -> tuple[list[Component], list[ClarifyingQuestion]]:
    components, seen = [], set()
    for comp in result.components[:MAX_COMPONENTS]:
        if comp.id in seen:
            continue
        seen.add(comp.id)
        components.append(comp)
    for comp in components:
        comp.connects = [cid for cid in comp.connects if cid in seen and cid != comp.id]
        comp.constraints = comp.constraints[:MAX_CONSTRAINTS]
        comp.search_query = " ".join(comp.search_query.split())[:100] or comp.description[:80]
    answered = {"monthly_budget": intake.monthly_budget is not None,
                "monthly_volume": intake.usage.monthly_volume is not None,
                "contacts": intake.usage.contacts is not None,
                "editors": intake.usage.editors is not None,
                "seats": True}
    questions = [q for q in result.questions if not answered.get(q.field, False)][:3]
    budget_caps = {"budget_planning", "budget_tracking", "financial_reporting", "budget_approvals"}
    if (intake.usage.seats > 1 and intake.usage.editors is None
            and any(c.capability in budget_caps for c in components)
            and not any(q.field == "editors" for q in questions)):
        questions.insert(0, ClarifyingQuestion(
            id="q_editors", field="editors",
            question=f"Of the {intake.usage.seats} people, how many need to create or edit budgets? The others can have view-only access if supported."))
    # Budget is required to rank options; ask for it even if the model did not.
    if not answered["monthly_budget"] and not any(q.field == "monthly_budget" for q in questions):
        questions.insert(0, ClarifyingQuestion(id="q_budget", field="monthly_budget",
                                               question="What is the most you want to spend per month on new software?"))
    return components, questions[:2]


def apply_answers(intake: Intake, questions: list[dict], answers: dict[str, str]) -> Intake:
    data = intake.model_dump()
    for q in questions:
        raw = str(answers.get(q["id"], "")).strip()
        if not raw:
            continue
        if q["field"] in ("monthly_budget", "monthly_volume", "contacts", "seats", "editors"):
            digits = "".join(ch for ch in raw.replace(",", "") if ch.isdigit() or ch == ".")
            try:
                number = float(digits)
            except ValueError:
                data["requirements"].append(f"{q['question']} {raw}")
                continue
            if q["field"] == "monthly_budget":
                data["monthly_budget"] = number
            else:
                data["usage"][q["field"]] = int(number)
        else:
            data["requirements"].append(f"{q['question']} {raw}"[:200])
    return Intake.model_validate(data)
