"""Flask routes for the consultation flow."""
from __future__ import annotations

from flask import Blueprint, current_app, jsonify, render_template, request
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
