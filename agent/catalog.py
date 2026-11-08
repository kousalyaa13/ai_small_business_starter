"""The in-house catalog of vetted tools and deterministic matching against it."""
from __future__ import annotations

import json
import re
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


# ---- Tools discovered by earlier web research (unvetted, reused until they go stale)

STOPWORDS = {
    "a", "an", "and", "any", "are", "as", "at", "be", "by", "for", "from", "in", "into", "is", "it", "its",
    "of", "on", "or", "our", "so", "that", "the", "their", "them", "they", "to", "via", "we", "when",
    "with", "without", "you", "your", "each", "every", "new", "automatically", "automated", "tool", "software",
}


def need_words(text: str) -> set[str]:
    words = set()
    for raw in re.findall(r"[a-z0-9]+", (text or "").lower()):
        if raw in STOPWORDS or len(raw) < 3:
            continue
        for suffix in ("ing", "es", "s"):
            if raw.endswith(suffix) and len(raw) - len(suffix) >= 3:
                raw = raw[: -len(suffix)]
                break
        words.add(raw)
    return words


def needs_match(a: str, b: str) -> bool:
    """Same need in different words: at least two shared content words covering 60% of the shorter."""
    wa, wb = need_words(a), need_words(b)
    shared = wa & wb
    return len(shared) >= 2 and len(shared) / max(1, min(len(wa), len(wb))) >= 0.6


def match_discovered(component: Component, discovered: list[dict], limit=3) -> list[Candidate]:
    """Reuse earlier web finds. Tagged capabilities match directly; 'other' needs match by wording."""
    from .research import looks_like_article  # local import: research imports catalog-free helpers only
    matches = []
    for tool in discovered:
        if looks_like_article(tool["site"]):
            continue  # saved from a blog post before articles were filtered
        if component.capability == "other":
            fits = any(needs_match(component.description, need) for need in tool["needs"])
        else:
            fits = component.capability in tool["capabilities"]
        if not fits:
            continue
        checked = tool["last_checked"][:10]
        matches.append(Candidate(
            tool_id=tool["tool_id"], name=tool["name"], source="web", vetted=False,
            capabilities=tool["capabilities"], site=tool["site"], description=tool.get("description") or "",
            plans=[Plan(**p) for p in tool["plans"]], pricing_source_url=tool.get("pricing_source_url") or "",
            pricing_checked_at=checked, evidence=tool["evidence"], audience=tool.get("audience") or "",
            setup_complexity=tool.get("setup_complexity") or "unknown", best_for=tool.get("best_for") or "",
            mentions=tool.get("mentions") or 0,
            notes=[f"Found by earlier web research (last checked {checked}) — not yet vetted by our team."]))

    def rank(c: Candidate):
        prices = [p.monthly_price for p in c.plans if p.monthly_price is not None]
        return (min(prices) if prices else float("inf"), c.name)

    ranked = sorted(matches, key=rank)
    return ranked if limit is None else ranked[:limit]
