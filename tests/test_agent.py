import tempfile
import threading
import unittest
from pathlib import Path

from agent.llm import StubModel
from agent.runner import InlineExecutor
from agent.safe_fetch import FetchError
from agent.schemas import Answers, ApprovalRequest, Intake, Usage
from agent.service import AgentService, Conflict
from agent.store import RunActive

EXAMPLE = ("Whenever someone fills out our contact form, add them to our CRM without creating duplicate "
           "contacts. Keep additional software costs under $30 per month.")


class NoNetworkFetcher:
    def fetch(self, url):
        raise FetchError("network disabled in tests")


def make_service(tmp, **kwargs):
    kwargs.setdefault("model", StubModel())
    kwargs.setdefault("search", None)
    kwargs.setdefault("fetcher", NoNetworkFetcher())
    return AgentService.create(Path(tmp) / "app.db", checkpoint_path=Path(tmp) / "cp.db",
                               executor=InlineExecutor(), **kwargs)


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def start_to_approval(self, svc):
        project_id, run_id = svc.start(Intake(problem_text=EXAMPLE))
        status = svc.status(project_id)
        self.assertEqual(status["run"]["status"], "waiting_input")
        self.assertEqual({q["field"] for q in status["questions"]}, {"monthly_budget", "monthly_volume"})
        svc.answer(project_id, Answers(answers={"q_budget": "$30", "q_volume": "200"}))
        status = svc.status(project_id)
        self.assertEqual(status["run"]["status"], "waiting_approval")
        self.assertEqual(status["requirement_version"], 2)
        return project_id, status

    def test_approval_survives_restart(self):
        project_id, status = self.start_to_approval(make_service(self.tmp.name))
        # Simulated restart: a fresh service, graph and checkpointer over the same files.
        restarted = make_service(self.tmp.name)
        approval, created = restarted.approve(project_id, ApprovalRequest(
            plan_version=status["plan"]["version"], option_id="opt1", max_build_budget=50, idempotency_key="key-123456"))
        self.assertTrue(created)
        final = restarted.status(project_id)
        self.assertEqual((final["run"]["status"], final["run"]["step"]), ("completed", "approved"))
        self.assertEqual(final["approval"]["recurring_cost"]["monthly_total"], 0.0)
        self.assertEqual(final["approval"]["max_build_budget"], 50)

    def test_repeated_approval_creates_one_record(self):
        svc = make_service(self.tmp.name)
        project_id, status = self.start_to_approval(svc)
        req = ApprovalRequest(plan_version=1, option_id="opt1", idempotency_key="key-aaaaaa")
        first, created1 = svc.approve(project_id, req)
        again, created2 = svc.approve(project_id, req)
        other_key, created3 = svc.approve(project_id, req.model_copy(update={"idempotency_key": "key-bbbbbb"}))
        self.assertEqual((created1, created2, created3), (True, False, False))
        self.assertEqual(first["id"], again["id"])
        self.assertEqual(first["id"], other_key["id"])
        self.assertEqual(svc.store.count_approvals(project_id), 1)
        with self.assertRaises(Conflict):
            svc.approve(project_id, ApprovalRequest(plan_version=1, option_id="opt2", idempotency_key="key-cccccc"))
        with self.assertRaises(Conflict):
            svc.approve(project_id, ApprovalRequest(plan_version=2, option_id="opt1", idempotency_key="key-dddddd"))

    def test_one_active_run_and_single_lease(self):
        svc = make_service(self.tmp.name)
        project_id, status = self.start_to_approval(svc)
        with self.assertRaises(RunActive):
            svc.store.create_run(project_id)
        run_id = status["run"]["id"]
        results = []
        barrier = threading.Barrier(5)

        def claim():
            barrier.wait()
            results.append(svc.store.acquire_lease(run_id, f"w{threading.get_ident()}", 60, ("waiting_approval",)))

        threads = [threading.Thread(target=claim) for _ in range(5)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(results.count(True), 1)

    def test_crashed_run_is_recovered(self):
        svc = make_service(self.tmp.name)
        project_id, run_id = svc.store.create_project(Intake(problem_text=EXAMPLE, monthly_budget=30,
                                                             usage=Usage(monthly_volume=50)).model_dump())
        # A worker claimed the run and died; its lease has expired.
        self.assertTrue(svc.store.acquire_lease(run_id, "dead-worker", -1, ("queued",)))
        self.assertIn(run_id, svc.store.recoverable_runs())
        svc.runner.recover()
        self.assertEqual(svc.status(project_id)["run"]["status"], "waiting_approval")

    def test_usage_is_recorded(self):
        svc = make_service(self.tmp.name)
        project_id, _ = svc.start(Intake(problem_text=EXAMPLE, monthly_budget=30, usage=Usage(monthly_volume=50)))
        usage = svc.status(project_id)["usage"]
        self.assertEqual(usage["model_calls"], 1)
        self.assertGreater(usage["tokens"], 0)


class ApiTests(unittest.TestCase):
    def setUp(self):
        import app as project
        self.tmp = tempfile.TemporaryDirectory()
        self.flask = project.app
        self.original = self.flask.extensions["agent_service"]
        self.flask.extensions["agent_service"] = make_service(self.tmp.name)
        self.client = self.flask.test_client()

    def tearDown(self):
        self.flask.extensions["agent_service"] = self.original
        self.tmp.cleanup()

    def test_flow_over_http(self):
        self.assertEqual(self.client.get("/consult").status_code, 200)
        self.assertEqual(self.client.post("/api/projects", json={"problem_text": "short"}).status_code, 400)
        r = self.client.post("/api/projects", json={"problem_text": EXAMPLE, "monthly_budget": 30,
                                                    "usage": {"monthly_volume": 100}})
        self.assertEqual(r.status_code, 202)
        pid = r.get_json()["project_id"]
        status = self.client.get(f"/api/projects/{pid}").get_json()
        self.assertEqual(status["run"]["status"], "waiting_approval")
        body = {"plan_version": status["plan"]["version"], "option_id": "opt1", "idempotency_key": "http-key-1"}
        self.assertEqual(self.client.post(f"/api/projects/{pid}/approve", json=body).status_code, 201)
        self.assertEqual(self.client.post(f"/api/projects/{pid}/approve", json=body).status_code, 200)
        self.assertEqual(self.client.post(f"/api/projects/{pid}/answers", json={"answers": {}}).status_code, 409)
        self.assertEqual(self.client.get("/api/projects/prj_missing").status_code, 404)


if __name__ == "__main__":
    unittest.main()
