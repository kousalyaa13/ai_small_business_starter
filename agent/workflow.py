"""The approved selection as a workflow: one step per tool, one arrow per connection.

Built in code from what the owner chose; nothing here calls a model.
"""
from __future__ import annotations

NO_TOOL = {"skipped", "existing", "none"}


def build_workflow(components: list[dict], option: dict) -> dict:
    by_need = {a["component_id"]: a for a in option.get("assignments", [])}
    connectors = [c for c in components if c["capability"] == "workflow_automation" and len(c.get("connects", [])) >= 2]
    connector_ids = {c["id"] for c in connectors}
    steps = []
    for comp in components:
        if comp["id"] in connector_ids:
            continue
        a = by_need.get(comp["id"], {})
        steps.append({"id": comp["id"], "need": comp["description"], "tool": a.get("tool_name", "Not chosen"),
                      "tool_id": a.get("tool_id"), "choice": a.get("source", "none"), "plan": a.get("plan_name"),
                      "monthly_cost": a.get("monthly_cost"), "site": a.get("site_url") or a.get("source_url") or "",
                      "pricing_url": a.get("pricing_source_url") or ""})
    step_by_id = {s["id"]: s for s in steps}
    edges = []
    for conn in connectors:
        src, dst = (step_by_id.get(cid) for cid in conn["connects"][:2])
        if not src or not dst:
            continue
        via = by_need.get(conn["id"], {})
        if src["choice"] in NO_TOOL or dst["choice"] in NO_TOOL:
            kind = "manual"  # one end is skipped or handled outside the plan
        elif src["tool_id"] and src["tool_id"] == dst["tool_id"]:
            kind = "built_in"  # both steps happen in the same tool
        elif via.get("source") in NO_TOOL or not via.get("tool_id"):
            kind = "manual"
        else:
            kind = "connector"
        edges.append({"id": conn["id"], "need": conn["description"], "from": src["id"], "to": dst["id"],
                      "kind": kind, "connector": via.get("tool_name") if kind == "connector" else None,
                      "connector_id": via.get("tool_id") if kind == "connector" else None})
    linked = {e["from"] for e in edges} | {e["to"] for e in edges}
    for s in steps:
        s["standalone"] = s["id"] not in linked and s["choice"] not in NO_TOOL
    return {"steps": steps, "edges": edges}
