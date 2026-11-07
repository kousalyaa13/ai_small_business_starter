import unittest

from agent import catalog
from agent.decompose import intake_prompt
from agent.llm import StubModel
from agent.options import build_options
from agent.pricing import choose_plan, option_total
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

    def test_owned_tool_wins_cost_ties(self):
        intake = Intake(problem_text=EXAMPLE, monthly_budget=30, existing_software=["Wix"])
        comps = self.decompose(EXAMPLE, monthly_budget=30).components
        cands = {c.id: [x for x in catalog.match(c, intake, self.catalog) if x.tool_id != "hubspot"] for c in comps}
        form = [a for a in build_options(comps, cands, intake)[0].assignments if a.capability == "form_capture"][0]
        self.assertEqual((form.tool_id, form.source, form.monthly_cost), ("wix", "owned", 0.0))


if __name__ == "__main__":
    unittest.main()
