import tempfile
import unittest
from pathlib import Path

from agent.connections import check_pair, core_name, route_for
from agent.llm import StubModel
from agent.research import SearchResult
from agent.runner import InlineExecutor
from agent.safe_fetch import FetchError, page_from_html
from agent.schemas import ApprovalRequest, Intake, Usage
from agent.service import AgentService
from agent.store import Store
from agent.workflow import build_workflow

EXAMPLE = ("Whenever someone fills out our contact form, add them to our CRM without creating duplicate "
           "contacts. Keep additional software costs under $30 per month.")

COMPONENTS = [
    {"id": "c1", "capability": "form_capture", "description": "Collect contact form submissions", "connects": []},
    {"id": "c2", "capability": "crm_contacts", "description": "Store leads without duplicates", "connects": []},
    {"id": "c3", "capability": "workflow_automation", "description": "Send new leads to the CRM", "connects": ["c1", "c2"]},
    {"id": "c4", "capability": "other", "description": "Schedule staff shifts", "connects": []},
]


def assignment(cid, tool_id, name, source="catalog", cost=0.0, site=""):
    return {"component_id": cid, "tool_id": tool_id, "tool_name": name, "source": source, "monthly_cost": cost,
            "plan_name": "Free", "site_url": site}


class Search:
    provider = "fake"

    def __init__(self, results):
        self.results, self.queries = results, []

    def search(self, query, count=8):
        self.queries.append(query)
        return [r for key, rs in self.results.items() if key in query for r in rs]


class Fetcher:
    def __init__(self, pages):
        self.pages = pages

    def fetch(self, url):
        if url not in self.pages:
            raise FetchError("not found")
        return page_from_html(url, self.pages[url])


class WorkflowTests(unittest.TestCase):
    def test_same_tool_on_both_ends_is_built_in(self):
        option = {"assignments": [assignment("c1", "hubspot", "HubSpot"), assignment("c2", "hubspot", "HubSpot"),
                                  assignment("c3", "hubspot", "Built into HubSpot", "native"),
                                  assignment("c4", "web:homebase", "Homebase", "web")]}
        wf = build_workflow(COMPONENTS, option)
        self.assertEqual([s["id"] for s in wf["steps"]], ["c1", "c2", "c4"])  # the connector is an arrow, not a step
        self.assertEqual((wf["edges"][0]["kind"], wf["edges"][0]["from"], wf["edges"][0]["to"]), ("built_in", "c1", "c2"))
        standalone = {s["tool"]: s["standalone"] for s in wf["steps"]}
        self.assertEqual(standalone, {"HubSpot": False, "Homebase": True})  # runs on its own

    def test_different_tools_need_a_connector_and_skips_are_manual(self):
        option = {"assignments": [assignment("c1", "mailchimp", "Mailchimp"), assignment("c2", "jobber", "Jobber", cost=49),
                                  assignment("c3", "zapier", "Zapier"), assignment("c4", None, "Skipped for now", "skipped")]}
        wf = build_workflow(COMPONENTS, option)
        edge = wf["edges"][0]
        self.assertEqual((edge["kind"], edge["connector"]), ("connector", "Zapier"))
        self.assertFalse([s for s in wf["steps"] if s["id"] == "c4"][0]["standalone"])  # skipped, not "on its own"
        skipped_end = {"assignments": [assignment("c1", None, "Using what you already have", "existing"),
                                       assignment("c2", "jobber", "Jobber"), assignment("c3", "zapier", "Zapier")]}
        self.assertEqual(build_workflow(COMPONENTS, skipped_end)["edges"][0]["kind"], "manual")


class ConnectionCheckTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "app.db")
        self.a = {"id": "c1", "tool": "Hostme", "site": "https://www.hostmeapp.com/"}
        self.b = {"id": "c2", "tool": "HubSpot", "site": "https://www.hubspot.com/"}

    def tearDown(self):
        self.tmp.cleanup()

    def test_native_integration_is_verified_by_a_quote(self):
        page = ("<html><head><title>Integrations</title></head><body><p>Hostme connects with HubSpot so every guest "
                "is synced to your CRM automatically.</p></body></html>")
        search = Search({"integration": [SearchResult("Hostme integrations", "https://www.hostmeapp.com/integrations", "")]})
        result = check_pair(self.store, search, Fetcher({"https://www.hostmeapp.com/integrations": page}), self.a, self.b)
        self.assertEqual(result["native"]["url"], "https://www.hostmeapp.com/integrations")
        self.assertIn("HubSpot", result["native"]["quote"])
        route = route_for({"kind": "connector"}, self.a, self.b, result)
        self.assertEqual(route["route"], "native")
        # Cached per pair of tools: a second check makes no searches.
        again = Search({})
        self.assertEqual(check_pair(self.store, again, Fetcher({}), self.b, self.a)["native"]["url"], result["native"]["url"])
        self.assertEqual(again.queries, [])

    def test_zapier_route_needs_both_app_pages(self):
        zap = lambda name: f"<html><head><title>{name} integrations</title></head><body><p>Connect {name} with 7,000 apps.</p></body></html>"
        search = Search({"zapier.com Hostme": [SearchResult("Hostme", "https://zapier.com/apps/hostme/integrations", "")],
                         "zapier.com HubSpot": [SearchResult("HubSpot", "https://zapier.com/apps/hubspot/integrations", "")]})
        fetcher = Fetcher({"https://zapier.com/apps/hostme/integrations": zap("Hostme"),
                           "https://zapier.com/apps/hubspot/integrations": zap("HubSpot")})
        fetcher.pages["https://zapier.com/apps/hostme/integrations/hubspot"] = (
            "<html><body><p>When a new reservation is made in Hostme, create a contact in HubSpot.</p></body></html>")
        result = check_pair(self.store, search, fetcher, self.a, self.b)
        route = route_for({"kind": "connector"}, self.a, self.b, result)
        self.assertEqual((route["route"], len(route["sources"])), ("zapier", 2))
        self.assertEqual(result["details"]["url"], "https://zapier.com/apps/hostme/integrations/hubspot")
        self.assertIn("new reservation", result["details"]["text"])

    def test_unverified_when_nothing_is_found_and_unchecked_without_search(self):
        result = check_pair(self.store, Search({}), Fetcher({}), self.a, self.b)
        self.assertEqual(route_for({"kind": "connector"}, self.a, self.b, result)["route"], "unverified")
        other = {"id": "c3", "tool": "Square Marketing", "site": "https://squareup.com/"}
        self.assertEqual(route_for({"kind": "connector"}, self.a, other, check_pair(self.store, None, None, self.a, other))["route"],
                         "unchecked")
        self.assertEqual(core_name("Square Marketing"), "square")

    def test_built_in_and_manual_routes_need_no_check(self):
        self.assertEqual(route_for({"kind": "built_in"}, self.b, self.b, None)["route"], "built_in")
        self.assertEqual(route_for({"kind": "manual"}, self.a, self.b, None)["route"], "manual")


class GuideTests(unittest.TestCase):
    """The guide may only use integration-specific names that appear on a real integration page."""

    def test_names_not_on_the_integration_page_get_a_note(self):
        from agent.budget import Limits, Meter
        from agent.schemas import Intake
        from agent.setup import FlowDetail, GuideStep, SetupGuide, build_setup_plan

        class Writer(StubModel):
            def structured(self, task, instructions, content, schema):
                self.prompt = content
                usage = StubModel.structured(self, "fit", "", "Business type: x", __import__("agent.schemas").schemas.FitAssessments)[1]
                return SetupGuide(flows=[FlowDetail(label="[E1] HubSpot to Jobber", when="A form is submitted", what_moves="Name and email",
                                                    how="Zap: New Form Submission, then Create Client")],
                                  steps=[GuideStep(title="Connect", tool="", substeps=["Build the Zap"],
                                                   references=["New Form Submission"]),
                                         GuideStep(title="Connect more", tool="", substeps=["Add a step"],
                                                   references=["Magic Sync Wizard"])]), usage

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store = Store(Path(tmp.name) / "app.db")
        pid, run = store.create_project(Intake(problem_text=EXAMPLE).model_dump())
        a = {"id": "c1", "tool": "HubSpot", "site": "https://www.hubspot.com/"}
        b = {"id": "c2", "tool": "Jobber", "site": "https://getjobber.com/"}
        from agent.connections import pair_key
        store.save_connection(pair_key(a, b), {"checked": True, "native": None,
                                               "zapier": {"c1": "https://zapier.com/apps/hubspot/integrations",
                                                          "c2": "https://zapier.com/apps/jobber/integrations"},
                                               "details": {"url": "https://zapier.com/apps/hubspot/integrations/jobber",
                                                           "text": "Trigger: New Form Submission in HubSpot. Action: Create Client in Jobber."}})
        option = {"assignments": [assignment("c1", "hubspot", "HubSpot", site=a["site"]),
                                  assignment("c2", "jobber", "Jobber", cost=None, site=b["site"]),
                                  assignment("c3", "zapier", "Zapier")]}
        writer = Writer()
        plan = build_setup_plan(Meter(store, run, pid, Limits()), writer, store, Search({}), Fetcher({}),
                                Intake(problem_text=EXAMPLE, business_type="plumbing"), COMPONENTS[:3], option, 0)
        self.assertIn("Integration details for [e1]", writer.prompt)       # the real page is given to the writer
        self.assertIn("price unknown: confirm with the vendor", writer.prompt)  # Jobber's price isn't listed
        self.assertTrue(plan["names_unconfirmed"])                         # "Magic Sync Wizard" is not on the page
        self.assertNotIn("note", plan["guide"][0])                          # one note above the guide, not per step
        self.assertEqual(plan["workflow"]["edges"][0]["how"], "Zap: New Form Submission, then Create Client")


class SetupPlanServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.svc = AgentService.create(Path(self.tmp.name) / "app.db", checkpoint_path=Path(self.tmp.name) / "cp.db",
                                       model=StubModel(), search=Search({}), fetcher=Fetcher({}), executor=InlineExecutor())
        # "plumbing": no catalog tool is rated built for it offline, so every tool stays visible to pick.
        self.pid, _ = self.svc.start(Intake(problem_text=EXAMPLE, monthly_budget=30, business_type="plumbing",
                                            usage=Usage(monthly_volume=50)))
        self.status = self.svc.status(self.pid)
        self.ids = {c["capability"]: c["id"] for c in self.status["components"]}

    def tearDown(self):
        self.tmp.cleanup()

    def approve(self, picks, key="setup-key-1"):
        return self.svc.approve(self.pid, ApprovalRequest(plan_version=self.status["plan"]["version"], option_id="custom",
                                                         selections=picks, max_build_budget=200, idempotency_key=key))

    def test_setup_plan_is_built_once_after_approval(self):
        picks = {self.ids["form_capture"]: "mailchimp", self.ids["crm_contacts"]: "jobber",
                 self.ids["workflow_automation"]: "zapier"}
        approval, _ = self.approve(picks)
        setup = self.svc.status(self.pid)["setup"]
        self.assertEqual(setup["status"], "ready")
        plan = setup["plan"]
        self.assertEqual({s["tool"] for s in plan["workflow"]["steps"]}, {"Mailchimp", "Jobber"})
        edge = plan["workflow"]["edges"][0]
        self.assertEqual((edge["kind"], edge["route"]["route"]), ("connector", "unverified"))
        self.assertTrue(edge["when"] and edge["what_moves"] and edge["how"])  # how the tools interact
        self.assertNotIn("details", edge)  # integration page excerpts are for the guide writer only
        guide = plan["guide"]
        self.assertTrue(guide and all(st["substeps"] for st in guide))
        self.assertNotIn("costs", plan)  # no rehash of prices the owner already saw
        # Checklist progress is saved on the server.
        self.svc.set_progress(self.pid, guide[0]["key"], True)
        self.assertEqual(self.svc.status(self.pid)["setup"]["done"], [guide[0]["key"]])
        # Approving again (same key) does not rebuild the plan.
        built = self.svc.store.setup_plan(approval["id"])["created_at"]
        self.approve(picks)
        self.assertEqual(self.svc.store.setup_plan(approval["id"])["created_at"], built)

    def test_a_plan_with_an_unlisted_price_can_be_saved_and_set_up(self):
        from agent.store import UnknownOption
        option = self.svc.price_selection(self.pid, {self.ids["form_capture"]: "mailchimp",
                                                     self.ids["crm_contacts"]: "jobber",
                                                     self.ids["workflow_automation"]: "zapier"}).model_dump()
        crm = [a for a in option["assignments"] if a["component_id"] == self.ids["crm_contacts"]][0]
        # A need with no choice at all can't be saved...
        gap = dict(option, assignments=[dict(a, source="none") if a is crm else a for a in option["assignments"]])
        with self.assertRaises(UnknownOption):
            self.svc.store.approve(self.pid, self.status["plan"]["version"], "custom", 0, "gap-key-1", [], custom_option=gap)
        # ...but one whose price couldn't be confirmed can, and the setup plan asks the owner to confirm it.
        crm["monthly_cost"], option["total_is_complete"] = None, False
        approval, created = self.svc.store.approve(self.pid, self.status["plan"]["version"], "custom", 0,
                                                   "unpriced-key-1", [], custom_option=option)
        self.assertTrue(created)
        self.svc.build_setup(self.pid, approval["id"])
        self.assertEqual(self.svc.status(self.pid)["setup"]["status"], "ready")

    def test_setup_builds_when_a_connected_need_is_already_handled(self):
        picks = {self.ids["form_capture"]: "have", self.ids["crm_contacts"]: "jobber", self.ids["workflow_automation"]: "zapier"}
        self.approve(picks, key="have-key-1")
        setup = self.svc.status(self.pid)["setup"]
        self.assertEqual(setup["status"], "ready")
        self.assertEqual(setup["plan"]["workflow"]["edges"][0]["route"]["route"], "manual")
        self.assertEqual([s["choice"] for s in setup["plan"]["workflow"]["steps"]][:2], ["existing", "catalog"])

    def test_connections_can_be_checked_before_saving(self):
        same = {self.ids["form_capture"]: "hubspot", self.ids["crm_contacts"]: "hubspot", self.ids["workflow_automation"]: "zapier"}
        self.assertEqual(self.svc.check_connections(self.pid, same)[0]["route"], "built_in")
        mixed = {self.ids["form_capture"]: "mailchimp", self.ids["crm_contacts"]: "jobber", self.ids["workflow_automation"]: "zapier"}
        result = self.svc.check_connections(self.pid, mixed)[0]
        self.assertEqual((result["from"], result["to"], result["route"]), ("Mailchimp", "Jobber", "unverified"))

    def test_endpoints(self):
        import app as project
        flask_app = project.app
        original = flask_app.extensions["agent_service"]
        flask_app.extensions["agent_service"] = self.svc
        try:
            client = flask_app.test_client()
            picks = {self.ids["form_capture"]: "hubspot", self.ids["crm_contacts"]: "hubspot"}
            r = client.post(f"/api/projects/{self.pid}/connections", json={"selections": picks})
            self.assertEqual((r.status_code, r.get_json()["connections"][0]["route"]), (200, "built_in"))
            self.assertEqual(client.post(f"/api/projects/{self.pid}/setup/progress", json={"key": "x:0", "done": True}).status_code, 409)
            self.approve(picks, key="endpoint-key-1")
            self.assertEqual(client.post(f"/api/projects/{self.pid}/setup/progress", json={"key": "x:0", "done": True}).status_code, 200)
            self.assertEqual(client.get(f"/api/projects/{self.pid}").get_json()["setup"]["done"], ["x:0"])
        finally:
            flask_app.extensions["agent_service"] = original


if __name__ == "__main__":
    unittest.main()
