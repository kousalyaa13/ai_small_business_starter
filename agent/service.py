"""Application-facing operations for the consultation flow."""
from __future__ import annotations

from pathlib import Path

from .budget import Limits, Meter
from .graph import Deps, build_graph, sqlite_checkpointer
from .llm import default_model
from .research import default_fetcher, default_search
from .runner import Runner
from .schemas import Answers, ApprovalRequest, Intake
from .store import Store

AUTHORIZED_ACTIONS = ["record_selected_plan"]  # build/deploy actions arrive in a later milestone


DEFAULT = object()  # sentinel: choose the client from environment configuration


class NotFound(Exception):
    pass


class Conflict(Exception):
    def __init__(self, message, payload=None):
        super().__init__(message)
        self.payload = payload


class AgentService:
    def __init__(self, store: Store, runner: Runner):
        self.store, self.runner = store, runner

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
        return cls(store, Runner(store, graph, executor=executor))

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
                                               "site": c["site"]} for c in (cands or [])[:3]]})
        usage = Meter(self.store, run["id"], project_id, Limits.from_env()).totals() if run else {}
        return {
            "project": {"id": project["id"], "created_at": project["created_at"]},
            "requirement_version": version, "intake": intake,
            "run": {"id": run["id"], "status": run["status"], "step": run["step"], "error": run["error"]} if run else None,
            "summary": snap.get("summary", ""),
            "questions": snap.get("questions", []) if run and run["status"] == "waiting_input" else [],
            "components": components, "research_notes": snap.get("research_notes", []),
            "plan": {"id": plan["id"], "version": plan["version"], **plan["payload"]} if plan else None,
            "approval": approval, "usage": usage,
        }
