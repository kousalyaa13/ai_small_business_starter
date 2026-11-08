import tempfile
import threading
import unittest
from pathlib import Path

from agent.llm import StubModel
from agent.runner import InlineExecutor
from agent.safe_fetch import FetchError
from agent.schemas import Answers, ApprovalRequest, Intake, ResearchRequest, RevisionRequest, Usage
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
        # Only the budget is asked up front; volume is asked later, if a shown plan's price depends on it.
        self.assertEqual({q["field"] for q in status["questions"]}, {"monthly_budget"})
        svc.answer(project_id, Answers(answers={"q_budget": "$30"}))
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

    def test_budget_consultation_can_be_repriced_without_losing_history(self):
        svc = make_service(self.tmp.name)
        request = ("Need to manage my budget development and tracking every fiscal year. "
                   "Budget of 75 per month. 5 people need access.")
        project_id, _ = svc.start(Intake(problem_text=request))
        first = svc.status(project_id)
        # Editor counts only change price, so they aren't asked up front; owners adjust them afterwards.
        self.assertEqual((first["run"]["status"], first["questions"]), ("waiting_approval", []))
        svc.revise(project_id, RevisionRequest(editors=3))
        first_plan = svc.status(project_id)
        self.assertEqual(first_plan["run"]["status"], "waiting_approval")
        self.assertEqual(first_plan["recovery"]["editors"], 3)
        self.assertTrue(first_plan["recovery"]["within_budget"])
        first_version = first_plan["plan"]["version"]
        version, _ = svc.revise(project_id, RevisionRequest(editors=5))
        updated = svc.status(project_id)
        self.assertEqual(updated["requirement_version"], version)
        self.assertGreater(updated["plan"]["version"], first_version)
        self.assertEqual(updated["recovery"]["editors"], 5)
        self.assertTrue(updated["recovery"]["near_misses"])
        with self.assertRaises(Conflict):
            svc.approve(project_id, ApprovalRequest(
                plan_version=first_version, option_id="opt1", idempotency_key="old-plan-approval"))

    def test_unresolved_gap_cannot_be_approved(self):
        svc = make_service(self.tmp.name)
        pid, _ = svc.start(Intake(problem_text="Create a custom spacecraft control system",
                                  monthly_budget=75))
        result = svc.status(pid)
        # No complete option exists, so the run ends instead of waiting for an approval that can't happen;
        # the recovery panel still offers ways to revise the request.
        self.assertEqual((result["run"]["status"], result["run"]["step"]), ("completed", "no_complete_option"))
        self.assertEqual(result["recovery"]["status"], "unrecognized")
        with self.assertRaises(ValueError):
            svc.approve(pid, ApprovalRequest(
                plan_version=result["plan"]["version"], option_id="opt1",
                idempotency_key="cannot-approve-gap"))

    def test_usage_is_recorded(self):
        svc = make_service(self.tmp.name)
        project_id, _ = svc.start(Intake(problem_text=EXAMPLE, monthly_budget=30, usage=Usage(monthly_volume=50)))
        usage = svc.status(project_id)["usage"]
        self.assertEqual(usage["model_calls"], 2)  # breakdown + one batched fit rating
        self.assertGreater(usage["tokens"], 0)
        # The same need for the same business type reuses cached fit ratings: no second rating call.
        again, _ = svc.start(Intake(problem_text=EXAMPLE, monthly_budget=30, usage=Usage(monthly_volume=50)))
        self.assertEqual(svc.status(again)["usage"]["model_calls"], 1)


class DiscoveryReuseTests(unittest.TestCase):
    """A need researched once is answered from the database next time, without searching."""

    PHONE = "Answer incoming phone calls and capture caller details for my plumbing business"
    PAGE = ("<html><head><title>CallDesk</title></head><body><p>CallDesk answers incoming phone calls and "
            "captures caller details for plumbing and other businesses.</p><p>Solo: $29/month</p></body></html>")

    class Search:
        provider = "fake"

        def __init__(self, results, fail=False):
            self.results, self.fail, self.calls = results, fail, 0

        def search(self, query, count=8):
            self.calls += 1
            if self.fail:
                raise AssertionError("web search should not run when a fresh discovery exists")
            return self.results

    class Fetcher:
        def __init__(self, pages):
            self.pages = pages

        def fetch(self, url):
            from agent.safe_fetch import page_from_html
            if url not in self.pages:
                raise FetchError("not found")
            return page_from_html(url, self.pages[url])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def first_run(self, intake):
        from agent.research import SearchResult
        first = make_service(self.tmp.name, search=self.Search([SearchResult("CallDesk", "https://calldesk.example/", "")]),
                             fetcher=self.Fetcher({"https://calldesk.example/": self.PAGE}))
        pid, _ = first.start(intake)
        status = first.status(pid)
        self.assertEqual(status["components"][0]["match"], "web")
        self.assertEqual(status["plan"]["options"][0]["assignments"][0]["tool_name"], "CallDesk")
        self.assertEqual(len(first.store.discovered_tools()), 1)

    def test_enough_saved_tools_skip_search(self):
        import os
        from unittest import mock
        intake = Intake(problem_text=self.PHONE, monthly_budget=50, usage=Usage(monthly_volume=40))
        with mock.patch.dict(os.environ, {"AGENT_MIN_ALTERNATIVES": "1"}):
            self.first_run(intake)
            offline = self.Search([], fail=True)
            second = make_service(self.tmp.name, search=offline, fetcher=self.Fetcher({}))
            pid, _ = second.start(intake)
            status = second.status(pid)
        self.assertEqual(offline.calls, 0)
        self.assertEqual(status["plan"]["options"][0]["assignments"][0]["tool_name"], "CallDesk")
        self.assertTrue(any("earlier web research" in n for n in status["research_notes"]))

    def test_too_few_saved_tools_are_topped_up_and_merged(self):
        from agent.research import SearchResult
        intake = Intake(problem_text=self.PHONE, monthly_budget=50, usage=Usage(monthly_volume=40))
        self.first_run(intake)  # saves CallDesk only; the default minimum is 3
        other = self.PAGE.replace("CallDesk", "RingBot").replace("$29", "$19")
        search = self.Search([SearchResult("RingBot", "https://ringbot.example/", "")])
        second = make_service(self.tmp.name, search=search, fetcher=self.Fetcher({"https://ringbot.example/": other}))
        pid, _ = second.start(intake)
        status = second.status(pid)
        self.assertGreater(search.calls, 0)
        self.assertEqual({c["name"] for c in status["components"][0]["candidates"]}, {"CallDesk", "RingBot"})
        self.assertEqual(len(second.store.discovered_tools()), 2)


class NoCompleteOptionTests(unittest.TestCase):
    def test_run_ends_with_no_option_instead_of_waiting(self):
        with tempfile.TemporaryDirectory() as tmp:
            svc = make_service(tmp)  # no search configured, so the call-answering part stays uncovered
            pid, _ = svc.start(Intake(problem_text="Answer incoming phone calls and capture caller details",
                                      monthly_budget=50, usage=Usage(monthly_volume=40)))
            status = svc.status(pid)
            self.assertEqual((status["run"]["status"], status["run"]["step"]), ("completed", "no_complete_option"))
            self.assertEqual(status["plan"]["options"], [])
            self.assertEqual(status["plan"]["gaps"], ["Answer incoming phone calls and capture caller details"])
            with self.assertRaises(ValueError):
                svc.approve(pid, ApprovalRequest(plan_version=1, option_id="opt1", idempotency_key="key-zzzzzz"))


class FitDrivenResearchTests(unittest.TestCase):
    def test_need_covered_only_by_workable_tools_is_researched(self):
        from agent.research import SearchResult
        page = ("<html><head><title>TableBook</title></head><body><p>TableBook lets customers book restaurant tables "
                "and appointments online and sends reminders for small restaurants.</p><p>Starter: $39/month</p></body></html>")
        search = ResearchMoreTests.Search([SearchResult("TableBook", "https://tablebook.example/", "")])
        with tempfile.TemporaryDirectory() as tmp:
            svc = make_service(tmp, search=search, fetcher=ResearchMoreTests.Fetcher({"https://tablebook.example/": page}))
            pid, _ = svc.start(Intake(problem_text="We need online booking for table reservations.",
                                      business_type="restaurant", monthly_budget=100))
            status = svc.status(pid)
        booking = [c for c in status["components"] if c["capability"] == "appointment_booking"][0]
        self.assertTrue(search.queries)  # Calendly/Jobber are only "workable" for a restaurant, so research ran
        self.assertTrue(any("weren't built for it" in n for n in status["research_notes"]))
        tools = {t["name"]: t for t in booking["tools"]}
        self.assertIn("TableBook", tools)
        self.assertEqual(tools["TableBook"]["fit"], "built_for_this")
        self.assertEqual(booking["tools"][0]["name"], "TableBook")  # purpose first in the list


class PlainNoteTests(unittest.TestCase):
    def test_plain_notes_replace_working_notes(self):
        from agent.service import plain_note
        tools = [{"fit": "built_for_this"}, {"fit": "built_for_this"}, {"fit": "workable"}]
        self.assertEqual(plain_note(tools, True, "Restaurant"),
                         "We searched the web for tools made for this and found 3 tools, including 2 made for restaurant businesses.")
        self.assertEqual(plain_note([{"fit": "workable"}], False, "restaurant"),
                         "We found 1 tool for this, but none made specifically for it, so check each one's fit.")
        self.assertIn("couldn't find a tool", plain_note([], True, ""))

    def test_status_carries_a_plain_note_per_need(self):
        with tempfile.TemporaryDirectory() as tmp:
            svc = make_service(tmp)
            pid, _ = svc.start(Intake(problem_text=EXAMPLE, monthly_budget=30, business_type="plumbing"))
            for comp in svc.status(pid)["components"]:
                self.assertTrue(comp["plain_note"].startswith("We "))


class BuiltForThisModel(StubModel):
    """Stand-in that rates every known tool as built for the job, so the catalog fully covers the needs."""

    def structured(self, task, instructions, content, schema):
        from agent.schemas import FitAssessment, FitAssessments
        if schema is FitAssessments:
            import re
            keys = re.findall(r"^\[(\w+)\]", content, re.M)
            return FitAssessments(assessments=[FitAssessment(key=k, fit="built_for_this", reason="Made for this.")
                                               for k in keys]), super().structured(task, instructions, "x", schema)[1]
        return super().structured(task, instructions, content, schema)


class ResearchMoreTests(unittest.TestCase):
    """The user can ask for cheaper or better tools even when known tools already cover a part."""

    PAGE = ("<html><head><title>LeadBox CRM</title></head><body><p>LeadBox is a simple CRM that stores customer "
            "contact records and leads for small businesses.</p><p>Solo: $9/month</p></body></html>")

    class Search:
        provider = "fake"

        def __init__(self, results):
            self.results, self.queries = results, []

        def search(self, query, count=8):
            self.queries.append(query)
            return self.results

    class Fetcher:
        def __init__(self, pages):
            self.pages = pages

        def fetch(self, url):
            from agent.safe_fetch import page_from_html
            if url not in self.pages:
                raise FetchError("not found")
            return page_from_html(url, self.pages[url])

    def setUp(self):
        from agent.research import SearchResult
        self.tmp = tempfile.TemporaryDirectory()
        self.search = self.Search([SearchResult("LeadBox CRM", "https://leadbox.example/", "")])
        self.svc = make_service(self.tmp.name, search=self.search, model=BuiltForThisModel(),
                                fetcher=self.Fetcher({"https://leadbox.example/": self.PAGE}))
        self.pid, _ = self.svc.start(Intake(problem_text=EXAMPLE, monthly_budget=30, business_type="plumbing",
                                            usage=Usage(monthly_volume=100)))

    def tearDown(self):
        self.tmp.cleanup()

    def crm_part(self, status):
        return [c for c in status["components"] if c["capability"] == "crm_contacts"][0]

    def test_search_finds_new_tools_for_a_covered_part(self):
        first = self.svc.status(self.pid)
        self.assertEqual(first["run"]["status"], "waiting_approval")
        self.assertEqual(self.search.queries, [])  # the catalog covered everything, so no search ran
        crm = self.crm_part(first)
        self.assertEqual(crm["match"], "catalog")

        self.svc.research_more(self.pid, ResearchRequest(component_ids=[crm["id"]]))
        second = self.svc.status(self.pid)
        self.assertEqual(second["run"]["status"], "waiting_approval")
        self.assertEqual(second["plan"]["version"], first["plan"]["version"] + 1)
        self.assertIn("LeadBox CRM", [c["name"] for c in self.crm_part(second)["candidates"]])
        self.assertIn("HubSpot", [c["name"] for c in self.crm_part(second)["candidates"]])  # existing tools kept
        # Only the chosen part was searched, with goal-directed queries for alternatives.
        self.assertTrue(any("alternatives for plumbing" in q for q in self.search.queries))
        self.assertTrue(any("affordable" in q for q in self.search.queries))
        self.assertTrue(any("new tool" in n for n in second["research_notes"]))
        with self.assertRaises(Conflict):  # the previous plan version can no longer be approved
            self.svc.approve(self.pid, ApprovalRequest(plan_version=first["plan"]["version"], option_id="opt1",
                                                       idempotency_key="old-version-key"))

    def test_no_search_after_approval_and_bad_part_rejected(self):
        status = self.svc.status(self.pid)
        with self.assertRaises(ValueError):
            self.svc.research_more(self.pid, ResearchRequest(component_ids=["c99"]))
        self.svc.approve(self.pid, ApprovalRequest(plan_version=status["plan"]["version"], option_id="opt1",
                                                   idempotency_key="approve-now-key"))
        with self.assertRaises(Conflict):
            self.svc.research_more(self.pid, ResearchRequest())

    def test_pricing_answers_are_applied_through_revise(self):
        self.svc.revise(self.pid, RevisionRequest(monthly_volume=500, contacts=2000))
        status = self.svc.status(self.pid)
        self.assertEqual((status["intake"]["usage"]["monthly_volume"], status["intake"]["usage"]["contacts"]), (500, 2000))

    def test_owner_selection_can_be_previewed_and_saved(self):
        status = self.svc.status(self.pid)
        ids = {c["capability"]: c["id"] for c in status["components"]}
        self.assertTrue(all(c.get("tools") for c in status["components"] if c["capability"] != "workflow_automation"))
        picks = {ids["form_capture"]: "have", ids["crm_contacts"]: "jobber", ids["workflow_automation"]: "zapier"}
        preview = self.svc.price_selection(self.pid, picks)
        self.assertEqual((preview.id, preview.monthly_total), ("custom", 49.0))
        approval, created = self.svc.approve(self.pid, ApprovalRequest(plan_version=status["plan"]["version"],
                                                                      option_id="custom", selections=picks,
                                                                      idempotency_key="custom-pick-key"))
        self.assertTrue(created)
        self.assertEqual(approval["option_id"], "custom")
        self.assertEqual(approval["recurring_cost"]["monthly_total"], 49.0)
        self.assertEqual([t["choice"] for t in approval["recurring_cost"]["tools"]], ["existing", "catalog", "catalog"])
        self.assertEqual(self.svc.status(self.pid)["run"]["status"], "completed")

    def test_selection_endpoint(self):
        import app as project
        flask_app = project.app
        original = flask_app.extensions["agent_service"]
        flask_app.extensions["agent_service"] = self.svc
        try:
            client = flask_app.test_client()
            status = client.get(f"/api/projects/{self.pid}").get_json()
            crm = [c for c in status["components"] if c["capability"] == "crm_contacts"][0]
            r = client.post(f"/api/projects/{self.pid}/selection", json={"selections": {crm["id"]: "jobber"}})
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.get_json()["option"]["id"], "custom")
            bad = client.post(f"/api/projects/{self.pid}/selection", json={"selections": {crm["id"]: "nope"}})
            self.assertEqual(bad.status_code, 400)
        finally:
            flask_app.extensions["agent_service"] = original

    def test_status_reports_which_need_is_being_searched(self):
        status = self.svc.status(self.pid)
        crm = [c for c in status["components"] if c["capability"] == "crm_contacts"][0]
        run_id = self.svc.store.start_followup_run(self.pid, {"components": status["components"],
                                                              "force_research": [crm["id"]], "goal": "both"})
        searching = self.svc.status(self.pid)
        self.assertEqual(searching["run"]["status"], "queued")
        self.assertEqual(searching["run"]["researching"], [crm["id"]])
        self.assertEqual(searching["plan"]["version"], status["plan"]["version"])  # current options stay visible
        self.svc.runner.drive(run_id)
        self.assertEqual(self.svc.status(self.pid)["run"]["researching"], [])

    def test_approval_blocked_while_search_runs(self):
        status = self.svc.status(self.pid)
        with self.svc.store.tx() as con:  # simulate a follow-up search in progress
            con.execute("UPDATE agent_runs SET status='running' WHERE id=?", (status["run"]["id"],))
        with self.assertRaises(Conflict):
            self.svc.approve(self.pid, ApprovalRequest(plan_version=status["plan"]["version"], option_id="opt1",
                                                       idempotency_key="during-search-key"))

    def test_research_endpoint(self):
        import app as project
        flask_app = project.app
        original = flask_app.extensions["agent_service"]
        flask_app.extensions["agent_service"] = self.svc
        try:
            client = flask_app.test_client()
            self.assertEqual(client.post(f"/api/projects/{self.pid}/research", json={"component_ids": ["nope"]}).status_code, 400)
            self.assertEqual(client.post(f"/api/projects/{self.pid}/research", json={"goal": "cheaper"}).status_code, 202)
            self.assertEqual(client.post(f"/api/projects/{self.pid}/research", json={"goal": "fastest"}).status_code, 400)
        finally:
            flask_app.extensions["agent_service"] = original


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

    def test_revision_endpoint(self):
        request = ("Need to manage my budget development and tracking every fiscal year. "
                   "Budget of 75 per month. 5 people need access.")
        r = self.client.post("/api/projects", json={"problem_text": request})
        self.assertEqual(r.status_code, 202)
        pid = r.get_json()["project_id"]
        initial = self.client.get(f"/api/projects/{pid}").get_json()
        self.assertEqual(initial["questions"], [])  # no editor question up front
        changed = self.client.post(f"/api/projects/{pid}/revise",
                                   json={"editors": 5, "monthly_budget": 75})
        self.assertEqual(changed.status_code, 202)
        result = self.client.get(f"/api/projects/{pid}").get_json()
        self.assertEqual(result["intake"]["usage"]["editors"], 5)
        self.assertEqual(result["recovery"]["status"], "within_budget")
        self.assertEqual(self.client.post(f"/api/projects/{pid}/revise",
                                          json={"editors": 8}).status_code, 400)

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
