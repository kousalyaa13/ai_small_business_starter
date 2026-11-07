"""The in-house catalog of vetted tools and deterministic matching against it."""
from __future__ import annotations

import json
from pathlib import Path

from .schemas import Candidate, Component, Intake, Plan

CATALOG_PATH = Path(__file__).resolve().parent.parent / "data" / "tools.json"


def load_catalog(path=CATALOG_PATH) -> list[dict]:
    return json.loads(Path(path).read_text())


def owned_by_customer(tool: dict, intake: Intake) -> bool:
    owned = {s.lower() for s in intake.existing_software}
    return any(name.lower() in owned for name in tool.get("existing_software_names", []))


def to_candidate(tool: dict, intake: Intake) -> Candidate:
    notes = [tool["pricing_notes"]] if tool.get("pricing_notes") else []
    return Candidate(
        tool_id=tool["id"], name=tool["name"], source="catalog", vetted=tool.get("status") == "vetted",
        capabilities=tool.get("capabilities", []), site=tool.get("site", ""), description=tool.get("description", ""),
        plans=[Plan(**p) for p in tool.get("plans", [])], pricing_source_url=tool.get("pricing_source_url", ""),
        pricing_checked_at=tool.get("pricing_checked_at", ""), owned=owned_by_customer(tool, intake),
        owned_plan_covers=bool(tool.get("existing_plan_covers", True)),
        integrations=tool.get("integrations", []), notes=notes,
        deployable_by_platform=bool(tool.get("deployable_by_platform", False)),
    )


def match(component: Component, intake: Intake, catalog: list[dict]) -> list[Candidate]:
    """Vetted catalog tools that provide the component's capability, best fit first."""
    owned = {s.lower() for s in intake.existing_software}
    matches = [to_candidate(t, intake) for t in catalog
               if t.get("status") == "vetted" and component.capability in t.get("capabilities", [])]

    def rank(c: Candidate):
        integrates = any(i.lower() in owned for i in c.integrations)
        prices = [p.monthly_price for p in c.plans if p.monthly_price is not None]
        cheapest = min(prices) if prices else float("inf")
        return (not c.owned, not integrates, cheapest, c.name)

    return sorted(matches, key=rank)
