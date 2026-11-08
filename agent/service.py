"""Application-facing operations for the consultation flow."""
from __future__ import annotations

from pathlib import Path

from .budget import Limits, Meter
from .catalog import load_catalog as default_catalog
from .recovery import recovery_for
from .graph import Deps, build_graph, sqlite_checkpointer
from .llm import default_model
from .research import default_fetcher, default_search
from .runner import Runner
from .schemas import Answers, ApprovalRequest, Intake, RevisionRequest
from .store import RunActive, Store

AUTHORIZED_ACTIONS = ["record_selected_plan"]  # build/deploy actions arrive in a later milestone


DEFAULT = object()  # sentinel: choose the client from environment configuration


class NotFound(Exception):
    pass


class Conflict(Exception):
    def __init__(self, message, payload=None):
        super().__init__(message)
        self.payload = payload


class AgentService:
    def __init__(self, store: Store, runner: Runner, catalog_loader=default_catalog):
        self.store, self.runner, self.catalog_loader = store, runner, catalog_loader

    @classmethod
    def create(cls, db_path, checkpoint_path=None, model=None, search=DEFAULT, fetcher=DEFAULT,
               limits=None, executor=None, load_catalog=None):
        """search/fetcher: DEFAULT picks from configured keys; pass None to disable search."""
        store = Store(db_path)
        checkpoint_path = checkpoint_path or Path(db_path).with_name("checkpoints.db")
        deps = Deps(store=store, model=model or default_model(),
                    search=default_search() if search is DEFAULT else search,
                    fetcher=default_fetcher() if fetcher is DEFAULT else fetcher,
                    limits=limits or Limits.from_env())
        if load_catalog:
            deps.load_catalog = load_catalog
        graph = build_graph(deps, sqlite_checkpointer(checkpoint_path))
        return cls(store, Runner(store, graph, executor=executor), deps.load_catalog)

    def _project(self, project_id, org_id):
        project = self.store.get_project(project_id)
        if not project or project["org_id"] != org_id:
            raise NotFound("Project not found.")
        return project

    def start(self, intake: Intake, org_id="default"):
        project_id, run_id = self.store.create_project(intake.model_dump(), org_id=org_id)
        self.runner.submit(run_id)
        return project_id, run_id

    def answer(self, project_id, body: Answers, org_id="default"):
        self._project(project_id, org_id)
        run = self.store.latest_run(project_id)
        if not run or run["status"] != "waiting_input":
            raise Conflict("This project is not waiting for answers.")
        self.runner.submit(run["id"], resume=body.answers, from_statuses=("waiting_input",))

    def revise(self, project_id, req: RevisionRequest, org_id="default"):
        """Save new assumptions and recompute options, preserving old versions."""
        self._project(project_id, org_id)
        _, previous = self.store.latest_requirements(project_id)
        data = dict(previous)
        changes = req.model_dump(exclude_unset=True, exclude_none=True)
        if not changes:
            raise ValueError("Change the budget, number of editors, team size or problem to try again.")
        usage = dict(data.get("usage") or {})
        for field in ("seats", "editors"):
            if field in changes:
                usage[field] = changes.pop(field)
        data["usage"] = usage
        data.update(changes)
        intake = Intake.model_validate(data)
        if intake.model_dump() == Intake.model_validate(previous).model_dump():
            raise ValueError("No requirements changed. Adjust a value to generate a new plan.")
        try:
            version, run_id = self.store.revise_project(project_id, intake.model_dump())
        except RunActive as exc:
            raise Conflict(str(exc)) from exc
        self.runner.submit(run_id)
        return version, run_id

    def approve(self, project_id, req: ApprovalRequest, org_id="default"):
        from .store import AlreadyApproved, StalePlan, UnknownOption
        self._project(project_id, org_id)
        try:
            approval, created = self.store.approve(project_id, req.plan_version, req.option_id, req.max_build_budget,
                                                   req.idempotency_key, AUTHORIZED_ACTIONS)
        except StalePlan as exc:
            raise Conflict(str(exc))
        except AlreadyApproved as exc:
            raise Conflict(str(exc), {"approval": exc.approval})
        except UnknownOption as exc:
            raise ValueError(str(exc))
        run = self.store.latest_run(project_id)
        if run and run["status"] == "waiting_approval":
            self.runner.submit(run["id"], resume={"approval_id": approval["id"]}, from_statuses=("waiting_approval",))
        return approval, created

    def status(self, project_id, org_id="default"):
        project = self._project(project_id, org_id)
        run = self.store.latest_run(project_id)
        if run and run["id"] in self.store.recoverable_runs():
            self.runner.recover()
            run = self.store.latest_run(project_id)
        version, intake = self.store.latest_requirements(project_id)
        plan = self.store.latest_plan(project_id)
        if plan and plan["requirement_version"] != version:
            plan = None  # don't display the previous plan while revised options are building
        approval = self.store.approval_for_plan(plan["id"]) if plan else None
        snap = run["snapshot"] if run else {}
        components = []
        for comp in snap.get("components", []):
            cands = snap.get("candidates", {}).get(comp["id"])
            if cands is None:
                match = "pending"
            elif not cands:
                match = "none"
            else:
                match = "catalog" if cands[0]["source"] == "catalog" else "web"
            components.append({**comp, "match": match,
                               "candidates": [{"name": c["name"], "source": c["source"], "vetted": c["vetted"],
                                               "site": c["site"]} for c in (cands or [])]})
        usage = Meter(self.store, run["id"], project_id, Limits.from_env()).totals() if run else {}
        recovery = recovery_for(Intake.model_validate(intake), snap.get("components", []), self.catalog_loader()) if plan else None
        return {
            "project": {"id": project["id"], "created_at": project["created_at"]},
            "requirement_version": version, "intake": intake,
            "run": {"id": run["id"], "status": run["status"], "step": run["step"], "error": run["error"]} if run else None,
            "summary": snap.get("summary", ""),
            "questions": snap.get("questions", []) if run and run["status"] == "waiting_input" else [],
            "components": components, "research_notes": snap.get("research_notes", []),
            "plan": {"id": plan["id"], "version": plan["version"], **plan["payload"]} if plan else None,
            "approval": approval, "usage": usage, "recovery": recovery,
        }
