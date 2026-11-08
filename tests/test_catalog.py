import unittest

from agent import catalog
from agent.decompose import intake_prompt
from agent.llm import StubModel
from agent.options import build_options
from agent.pricing import choose_plan, option_total, plan_cost, plan_fits
from agent.recovery import recovery_for
from agent.schemas import Component, Decomposition, Intake, Plan, Usage

EXAMPLE = ("Whenever someone fills out our contact form, add them to our CRM without creating duplicate "
           "contacts. Keep additional software costs under $30 per month.")


class PricingTests(unittest.TestCase):
    plans = [Plan(name="Free", monthly_price=0, included={"contacts": 250}),
             Plan(name="Pro", monthly_price=20, included={"contacts": 5000}),
             Plan(name="Team", monthly_price=8, per_seat=True)]

    def test_cheapest_fitting_plan(self):
        self.assertEqual(choose_plan(self.plans, Usage(contacts=100)).plan.name, "Free")
        # Free's contact limit is exceeded; per-seat Team (2 x $8) beats Pro ($20).
        choice = choose_plan(self.plans, Usage(contacts=1000, seats=2))
        self.assertEqual((choice.plan.name, choice.monthly_cost), ("Team", 16.0))

    def test_unknown_price_is_never_guessed(self):
        choice = choose_plan([Plan(name="Pro", monthly_price=None)], Usage())
        self.assertIsNone(choice.monthly_cost)
        self.assertEqual(choose_plan([], Usage()).plan, None)

    def test_read_only_viewers_are_free_only_when_verified(self):
        plan = Plan(name="Team", monthly_price=20, billing="annual",
                    per_seat=True, free_viewers=True)
        team = Usage(seats=5, editors=3)
        self.assertEqual(plan_cost(plan, team), 60)
        self.assertEqual(plan_cost(plan.model_copy(update={"free_viewers": False}), team), 100)

    def test_minimum_paid_members(self):
        business = Plan(name="Business", monthly_price=19, per_seat=True,
                        free_viewers=True, min_seats=3)
        self.assertFalse(plan_fits(business, Usage(seats=5, editors=2))[0])
        self.assertTrue(plan_fits(business, Usage(seats=5, editors=3))[0])

    def test_option_totals(self):
        self.assertEqual(option_total({"a": 10, "b": 15}, 30), (25, True, True))
        self.assertEqual(option_total({"a": 10, "b": None}, 30), (10, False, None))
        self.assertEqual(option_total({"a": 40, "b": None}, 30), (40, False, False))


class CatalogAndOptionsTests(unittest.TestCase):
    def setUp(self):
        self.catalog = catalog.load_catalog()

    def decompose(self, text, **intake):
        result, _ = StubModel().structured("decompose", "", intake_prompt(Intake(problem_text=text, **intake), 0), Decomposition)
        return result

    def test_catalog_match_and_gaps(self):
        intake = Intake(problem_text=EXAMPLE)
        crm = catalog.match(Component(id="c1", capability="crm_contacts", description="", constraints=[], connects=[]),
                            intake, self.catalog)
        self.assertEqual(crm[0].name, "HubSpot")
        self.assertTrue(all(c.vetted and c.source == "catalog" for c in crm))
        other = catalog.match(Component(id="c2", capability="other", description="", constraints=[], connects=[]),
                              intake, self.catalog)
        self.assertEqual(other, [])

    def test_owned_software_ranks_first(self):
        intake = Intake(problem_text=EXAMPLE, existing_software=["Wix"])
        forms = catalog.match(Component(id="c1", capability="form_capture", description="", constraints=[], connects=[]),
                              intake, self.catalog)
        self.assertEqual(forms[0].tool_id, "wix")
        self.assertTrue(forms[0].owned)

    def test_options_for_example(self):
        intake = Intake(problem_text=EXAMPLE, monthly_budget=30, usage=Usage(monthly_volume=200))
        comps = self.decompose(EXAMPLE, monthly_budget=30).components
        cands = {c.id: catalog.match(c, intake, self.catalog) for c in comps}
        options = build_options(comps, cands, intake)
        self.assertGreaterEqual(len(options), 2)
        best = options[0]
        self.assertEqual((best.monthly_total, best.within_budget), (0.0, True))
        self.assertIn("native", [a.source for a in best.assignments])  # HubSpot form -> HubSpot CRM needs no connector
        alternative = [o for o in options if o.title.startswith("Alternative")][0]
        self.assertNotIn("hubspot", [a.tool_id for a in alternative.assignments])
        self.assertFalse(any(o.deployable_by_platform for o in options))

    def test_budget_near_miss_with_five_editors(self):
        intake = Intake(problem_text="Develop and track annual fiscal budgets for five staff.",
                        monthly_budget=75, usage=Usage(seats=5, editors=5))
        components = [{"capability": "budget_planning"}, {"capability": "budget_tracking"}]
        result = recovery_for(intake, components, self.catalog)
        business = next(p for p in result["near_misses"]
                        if p["tool_id"] == "smartsheet" and p["plan_name"] == "Business (annual billing)")
        self.assertEqual((business["monthly_cost"], business["extra_per_month"]), (95, 20))
        airtable = next(p for p in result["near_misses"]
                        if p["tool_id"] == "airtable" and p["billing"] == "annual")
        self.assertEqual((airtable["monthly_cost"], airtable["extra_per_month"]), (100, 25))

    def test_budget_three_editors_two_viewers(self):
        intake = Intake(problem_text="Develop and track annual fiscal budgets for five staff.",
                        monthly_budget=75, usage=Usage(seats=5, editors=3))
        components = [{"capability": "budget_planning"}, {"capability": "budget_tracking"}]
        result = recovery_for(intake, components, self.catalog)
        airtable = next(p for p in result["within_budget"]
                        if p["tool_id"] == "airtable" and p["billing"] == "annual")
        self.assertEqual(airtable["monthly_cost"], 60)
        self.assertEqual(result["viewers"], 2)
        self.assertEqual(result["status"], "within_budget")

    def test_unrecognized_request_never_invents_a_budget_fix(self):
        intake = Intake(problem_text="Build a custom spacecraft launch control system",
                        monthly_budget=75, usage=Usage(seats=5))
        result = recovery_for(intake, [{"capability": "other"}], self.catalog)
        self.assertEqual(result["status"], "unrecognized")
        self.assertEqual(result["plans"], [])
        self.assertTrue(any("Clarify" in x for x in result["suggestions"]))

    def test_owned_tool_wins_cost_ties(self):
        intake = Intake(problem_text=EXAMPLE, monthly_budget=30, existing_software=["Wix"])
        comps = self.decompose(EXAMPLE, monthly_budget=30).components
        cands = {c.id: [x for x in catalog.match(c, intake, self.catalog) if x.tool_id != "hubspot"] for c in comps}
        form = [a for a in build_options(comps, cands, intake)[0].assignments if a.capability == "form_capture"][0]
        self.assertEqual((form.tool_id, form.source, form.monthly_cost), ("wix", "owned", 0.0))


if __name__ == "__main__":
    unittest.main()
