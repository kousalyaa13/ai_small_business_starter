"""Flask routes for the consultation flow."""
from __future__ import annotations

from flask import Blueprint, current_app, jsonify, render_template, request, send_file
from pydantic import ValidationError

from .schemas import Answers, ApprovalRequest, Intake, ResearchRequest, RevisionRequest, SelectionRequest
from .service import Conflict, NotFound

bp = Blueprint("agent", __name__)


def service():
    return current_app.extensions["agent_service"]


def _validation_error(exc: ValidationError):
    first = exc.errors()[0]
    field = ".".join(str(p) for p in first["loc"]) or "request"
    return jsonify({"error": f"{field}: {first['msg']}"}), 400


@bp.errorhandler(NotFound)
def _not_found(exc):
    return jsonify({"error": str(exc)}), 404


@bp.errorhandler(Conflict)
def _conflict(exc):
    return jsonify({"error": str(exc), **(exc.payload or {})}), 409


@bp.get("/consult")
def consult():
    return render_template("consult.html")


@bp.post("/api/projects")
def create_project():
    try:
        intake = Intake.model_validate(request.get_json(silent=True) or {})
    except ValidationError as exc:
        return _validation_error(exc)
    project_id, run_id = service().start(intake)
    return jsonify({"project_id": project_id, "run_id": run_id}), 202


@bp.get("/api/projects/<project_id>")
def project_status(project_id):
    return jsonify(service().status(project_id))


@bp.post("/api/projects/<project_id>/answers")
def answer(project_id):
    try:
        body = Answers.model_validate(request.get_json(silent=True) or {})
    except ValidationError as exc:
        return _validation_error(exc)
    service().answer(project_id, body)
    return jsonify({"status": "accepted"}), 202


@bp.post("/api/projects/<project_id>/revise")
def revise(project_id):
    try:
        body = RevisionRequest.model_validate(request.get_json(silent=True) or {})
        version, run_id = service().revise(project_id, body)
    except ValidationError as exc:
        return _validation_error(exc)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"status": "accepted", "requirement_version": version, "run_id": run_id}), 202


@bp.post("/api/projects/<project_id>/research")
def research_more(project_id):
    try:
        body = ResearchRequest.model_validate(request.get_json(silent=True) or {})
        run_id = service().research_more(project_id, body)
    except ValidationError as exc:
        return _validation_error(exc)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"status": "accepted", "run_id": run_id}), 202


@bp.post("/api/projects/<project_id>/selection")
def price_selection(project_id):
    """Price the owner's picks (no saving); the page calls this as picks change."""
    try:
        body = SelectionRequest.model_validate(request.get_json(silent=True) or {})
        option = service().price_selection(project_id, body.selections)
    except ValidationError as exc:
        return _validation_error(exc)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"option": option.model_dump()})


@bp.post("/api/projects/<project_id>/connections")
def check_connections(project_id):
    """Before saving: can the picked tools exchange data where they need to?"""
    try:
        body = SelectionRequest.model_validate(request.get_json(silent=True) or {})
        connections = service().check_connections(project_id, body.selections)
    except ValidationError as exc:
        return _validation_error(exc)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"connections": connections})


@bp.post("/api/projects/<project_id>/setup/progress")
def setup_progress(project_id):
    body = request.get_json(silent=True) or {}
    key = str(body.get("key", "")).strip()
    if not key:
        return jsonify({"error": "Missing checklist step."}), 400
    service().set_progress(project_id, key, bool(body.get("done")))
    return jsonify({"status": "saved"})


@bp.post("/api/projects/<project_id>/setup/retry")
def setup_retry(project_id):
    service().retry_setup(project_id)
    return jsonify({"status": "accepted"}), 202


@bp.post("/api/projects/<project_id>/approve")
def approve(project_id):
    try:
        req = ApprovalRequest.model_validate(request.get_json(silent=True) or {})
        approval, created = service().approve(project_id, req)
    except ValidationError as exc:
        return _validation_error(exc)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"approval": approval, "created": created}), 201 if created else 200


@bp.get("/api/projects/<project_id>/plan.pdf")
def download_plan_pdf(project_id):
    """Generate a fresh, downloadable PDF from the approved plan and its setup guide."""
    import io
    from datetime import date
    from html import escape
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, KeepTogether, HRFlowable, CondPageBreak

    state = service().status(project_id)
    approval = state.get("approval")
    if not approval:
        return jsonify({"error": "Approve your tool selection before downloading the plan."}), 409

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=(612, 792), leftMargin=46, rightMargin=46,
                            topMargin=66, bottomMargin=54, title="BloomBots Business Setup Plan")
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="BloomTitle", parent=styles["Title"], fontSize=21, leading=26,
                              textColor=colors.HexColor("#3C2850"), spaceAfter=12))
    styles.add(ParagraphStyle(name="BloomHeading", parent=styles["Heading2"], fontSize=13,
                              textColor=colors.HexColor("#7040B7"), spaceBefore=16, spaceAfter=7))
    styles.add(ParagraphStyle(name="BloomBody", parent=styles["BodyText"], fontSize=9.5,
                              leading=14, spaceAfter=6, textColor=colors.HexColor("#30415A")))
    styles.add(ParagraphStyle(name="BloomSmall", parent=styles["BloomBody"], fontSize=8, leading=11))
    styles.add(ParagraphStyle(name="BloomStep", parent=styles["BloomBody"], fontName="Helvetica-Bold", fontSize=10.5, leading=15, spaceBefore=9, spaceAfter=6, keepWithNext=True))
    styles.add(ParagraphStyle(name="BloomBullet", parent=styles["BloomBody"], fontSize=9, leading=14, leftIndent=14, firstLineIndent=-10, spaceAfter=7))
    story = []
    def p(value, style="BloomBody"):
        story.append(Paragraph(escape(str(value or "")).replace("\\n", "<br/>"), styles[style]))
    def heading(value):
        p(value, "BloomHeading")
    def fmt(value):
        try:
            return "${:,.2f}".format(float(value))
        except (ValueError, TypeError):
            return "Not verified"

    p("Your business setup plan", "BloomTitle")
    p("Prepared " + date.today().strftime("%B %d, %Y") + "  •  Keep this guide as your implementation checklist.", "BloomSmall")
    intake = state.get("intake") or {}
    if intake.get("problem_text"):
        heading("Your goal")
        p(intake["problem_text"])
    recurring = approval.get("recurring_cost") or {}
    heading("01  |  Your budget at a glance")
    p("Estimated known monthly subtotal: " + fmt(recurring.get("monthly_total")) +
      "   |   One-time setup budget: " + fmt(approval.get("max_build_budget")))
    p("Estimated subtotal only — tools without verified prices are excluded. Confirm current prices, billing terms, and integrations on official vendor sites.", "BloomSmall")
    heading("02  |  Your selected tools")
    selected = recurring.get("tools") or []
    if selected:
        dedup = {}
        for item in selected:
            name = item.get("tool")
            if not name or item.get("choice") in ("native", "skipped", "existing"):
                continue
            if name not in dedup:
                dedup[name] = {"tool": name, "need": []}
            need = item.get("need")
            if need and need not in dedup[name]["need"]:
                dedup[name]["need"].append(need)
        selected = [{"tool": item["tool"], "need": "; ".join(item["need"])} for item in dedup.values()]
        rows = [[Paragraph("<b>Tool</b>", styles["BloomSmall"]),
                 Paragraph("<b>What it helps with</b>", styles["BloomSmall"])]]
        for item in selected:
            rows.append([Paragraph(escape(str(item.get("tool") or "Not specified")), styles["BloomSmall"]),
                         Paragraph(escape(str(item.get("need") or "Selected")), styles["BloomSmall"])])
        table = Table(rows, colWidths=[175, 345], repeatRows=1, hAlign="LEFT")
        table.setStyle(TableStyle([("BACKGROUND", (0,0),(-1,0),colors.HexColor("#F0EAF9")),
            ("ROWBACKGROUNDS",(0,1),(-1,-1),[colors.white,colors.HexColor("#FAF8FD")]),("VALIGN",(0,0),(-1,-1),"TOP"),("BOTTOMPADDING",(0,0),(-1,-1),9),
            ("TOPPADDING",(0,0),(-1,-1),8),("LINEBELOW",(0,0),(-1,-1),.4,colors.HexColor("#E5DDF0"))]))
        story.append(table)
    setup = state.get("setup") or {}
    plan = setup.get("plan") or {}
    workflow = plan.get("workflow") or {}
    flow_steps = {s.get("id"): s.get("tool") for s in workflow.get("steps", [])}
    edges = [e for e in workflow.get("edges", []) if e.get("kind") != "manual"]
    if edges:
        heading("03  |  How your tools connect")
        for edge in edges:
            route = edge.get("route") or {}
            source = flow_steps.get(edge.get("from"), "Source")
            target = flow_steps.get(edge.get("to"), "Destination")
            via = " via Zapier" if route.get("route") == "zapier" else ""
            p(source + " → " + target + via + "  |  " + str(route.get("label") or "Connection"))
            if edge.get("what_moves"):
                p(edge["what_moves"], "BloomSmall")
    guide = plan.get("guide") or []
    if guide:
        heading("04  |  Your step-by-step setup checklist")
        completed = set(setup.get("done") or [])
        for index, step in enumerate(guide, 1):
            marker = "[X]" if step.get("key") in completed else "[ ]"
            story.append(CondPageBreak(100))
            p(f"{index:02d}. {step.get('title', 'Setup task')}", "BloomStep")
            p("Completed" if step.get("key") in completed else "To do", "BloomSmall")
            if step.get("tool"):
                p("Tool: " + step["tool"], "BloomSmall")
            for substep in step.get("substeps") or []:
                p("• " + str(substep), "BloomBullet")
    else:
        heading("Next steps")
        for step in ["Confirm official pricing and features for each selected tool.",
                     "Create your vendor accounts and configure your tools.",
                     "Test each integration with sample data before going live."]:
            p("[ ] " + step)
        if setup.get("status") == "building":
            p("Your personalized setup checklist is still being generated. Download again when it is ready.", "BloomSmall")
    heading("Before purchasing")
    p("Verify pricing, subscription commitments, features, privacy terms, and integrations directly with each vendor. BloomBots does not purchase, install, or connect software for you.")
    def decorate(canvas, document):
        canvas.saveState()
        canvas.setFillColor(colors.HexColor("#7443C8"))
        canvas.rect(0, 782, 612, 10, fill=1, stroke=0)
        canvas.setFont("Helvetica-Bold", 10)
        canvas.setFillColor(colors.HexColor("#342344"))
        canvas.drawString(46, 754, "BloomBots")
        canvas.setFont("Helvetica", 8)
        canvas.drawRightString(566, 754, "BUSINESS SETUP PLAN")
        canvas.setStrokeColor(colors.HexColor("#E5DDF0"))
        canvas.line(46, 744, 566, 744)
        canvas.line(46, 40, 566, 40)
        canvas.setFont("Helvetica", 8)
        canvas.drawString(46, 27, "Your tools. Your plan. Your next steps.")
        canvas.drawRightString(566, 27, f"Page {document.page}")
        canvas.restoreState()
    try:
        doc.build(story, onFirstPage=decorate, onLaterPages=decorate)
    except Exception:
        current_app.logger.exception("PDF generation failed for project %s", project_id)
        return jsonify({"error": "PDF generation failed. Check the Flask server logs for details."}), 500
    buffer.seek(0)
    return send_file(buffer, mimetype="application/pdf", as_attachment=True,
                     download_name="BloomBots-Business-Setup-Plan.pdf")
