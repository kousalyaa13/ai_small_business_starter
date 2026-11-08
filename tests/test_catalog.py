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
        crm = catalog.match(Component(id="c1", capability="crm_contacts", description="", constraints=[], connects=[], search_query=""),
                            intake, self.catalog)
        self.assertEqual(crm[0].name, "HubSpot")
        self.assertTrue(all(c.vetted and c.source == "catalog" for c in crm))
        other = catalog.match(Component(id="c2", capability="other", description="", constraints=[], connects=[], search_query=""),
                              intake, self.catalog)
        self.assertEqual(other, [])

    def test_discovered_tools_match_by_found_for_or_wording(self):
        tool = {"tool_id": "web:calldesk.example", "name": "CallDesk", "site": "https://calldesk.example/",
                "description": "", "capabilities": ["support_chat"], "found_for": ["support_chat", "other"],
                "plans": [{"name": "Solo", "monthly_price": 29}],
                "evidence": [], "pricing_source_url": "", "last_checked": "2026-11-07T00:00:00+00:00",
                "needs": ["Answer incoming phone calls and capture caller details"]}
        same_need = Component(id="c1", capability="other", description="Answer phone calls and capture leads",
                              constraints=[], connects=[], search_query="")
        other_need = Component(id="c2", capability="other", description="Send invoices after each job",
                               constraints=[], connects=[], search_query="")
        tagged = Component(id="c3", capability="support_chat", description="Chat", constraints=[], connects=[], search_query="")
        found = catalog.match_discovered(same_need, [tool])
        self.assertEqual([(c.name, c.source, c.vetted) for c in found], [("CallDesk", "web", False)])
        self.assertIn("earlier web research", found[0].notes[0])
        self.assertEqual(catalog.match_discovered(other_need, [tool]), [])
        self.assertEqual(len(catalog.match_discovered(tagged, [tool])), 1)

    def test_saved_tools_are_reused_only_for_the_kind_of_need_they_were_found_for(self):
        scheduler = {"tool_id": "web:deputy.example", "name": "Deputy", "site": "https://deputy.example/",
                     "description": "Staff scheduling", "capabilities": ["appointment_booking", "workflow_automation"],
                     "found_for": ["other"], "needs": ["Create staff shift schedules"], "plans": [], "evidence": [],
                     "pricing_source_url": "", "last_checked": "2026-11-08T00:00:00+00:00"}
        booking = Component(id="c1", capability="appointment_booking", description="Let customers book tables online",
                            constraints=[], connects=[], search_query="")
        sync = Component(id="c2", capability="workflow_automation", description="Move bookings between apps",
                         constraints=[], connects=[], search_query="")
        shifts = Component(id="c3", capability="other", description="Create and share staff shift schedules",
                           constraints=[], connects=[], search_query="")
        self.assertEqual(catalog.match_discovered(booking, [scheduler]), [])  # tagged, but not found for booking
        self.assertEqual(catalog.match_discovered(sync, [scheduler]), [])
        self.assertEqual([c.name for c in catalog.match_discovered(shifts, [scheduler])], ["Deputy"])

    def test_owned_software_ranks_first(self):
        intake = Intake(problem_text=EXAMPLE, existing_software=["Wix"])
        forms = catalog.match(Component(id="c1", capability="form_capture", description="", constraints=[], connects=[], search_query=""),
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

    def test_options_with_an_uncovered_part_are_not_offered(self):
        intake = Intake(problem_text=EXAMPLE, monthly_budget=30)
        crm = Component(id="c1", capability="crm_contacts", description="CRM", constraints=[], connects=[], search_query="")
        calls = Component(id="c2", capability="other", description="Answer phone calls", constraints=[], connects=[], search_query="")
        web_tool = catalog.Candidate(tool_id="web:calldesk.example", name="CallDesk", source="web", vetted=False,
                                     capabilities=["other"], plans=[Plan(name="Solo", monthly_price=29)])
        cands = {"c1": catalog.match(crm, intake, self.catalog), "c2": [web_tool]}
        options = build_options([crm, calls], cands, intake)
        self.assertTrue(options)
        for option in options:  # "Vetted tools only" would leave the call part blank, so it is dropped
            self.assertNotIn("none", [a.source for a in option.assignments])
            self.assertNotEqual(option.title, "Vetted tools only")
        self.assertEqual(build_options([crm, calls], {"c1": cands["c1"], "c2": []}, intake), [])

    def test_recommendations_include_official_product_links(self):
        intake = Intake(problem_text=EXAMPLE, monthly_budget=30, usage=Usage(monthly_volume=200))
        comps = self.decompose(EXAMPLE, monthly_budget=30).components
        cands = {c.id: catalog.match(c, intake, self.catalog) for c in comps}
        options = build_options(comps, cands, intake)
        assignment = next(a for a in options[0].assignments if a.source == "catalog")
        self.assertTrue(assignment.site_url.startswith("https://"))
        self.assertTrue(assignment.pricing_source_url.startswith("https://"))
        self.assertTrue(assignment.tool_description)

    def test_each_part_lists_the_other_tools_that_fit(self):
        intake = Intake(problem_text=EXAMPLE, monthly_budget=30, usage=Usage(monthly_volume=200))
        comps = self.decompose(EXAMPLE, monthly_budget=30).components
        cands = {c.id: catalog.match(c, intake, self.catalog) for c in comps}
        crm = [a for a in build_options(comps, cands, intake)[0].assignments if a.capability == "crm_contacts"][0]
        self.assertEqual(crm.tool_name, "HubSpot")
        self.assertIn("Jobber", [alt["name"] for alt in crm.alternatives])
        self.assertNotIn("HubSpot", [alt["name"] for alt in crm.alternatives])
        jobber = [alt for alt in crm.alternatives if alt["name"] == "Jobber"][0]
        self.assertEqual((jobber["plan_name"], jobber["monthly_cost"], jobber["vetted"]), ("Core", 49.0, True))

    def test_developer_tools_are_alternatives_not_the_pick(self):
        intake = Intake(problem_text=EXAMPLE, monthly_budget=100)
        calls = Component(id="c1", capability="other", description="Answer phone calls", constraints=[], connects=[], search_query="")
        dev = catalog.Candidate(tool_id="web:vapi.example", name="Vapi", source="web", vetted=False, capabilities=[],
                                setup_complexity="developer", plans=[Plan(name="Pay as you go", monthly_price=5)])
        easy = catalog.Candidate(tool_id="web:goodcall.example", name="Goodcall", source="web", vetted=False,
                                 capabilities=[], setup_complexity="no-code", plans=[Plan(name="Starter", monthly_price=59)])
        best = build_options([calls], {"c1": [dev, easy]}, intake)[0].assignments[0]
        self.assertEqual(best.tool_name, "Goodcall")
        self.assertEqual([(a["name"], a["setup_complexity"]) for a in best.alternatives], [("Vapi", "developer")])

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

    def test_monthly_limits_from_vendor_pages_filter_plans(self):
        plans = [Plan(name="Starter", monthly_price=29, included={"monthly_bookings": 300}),
                 Plan(name="Pro", monthly_price=79, included={"monthly_bookings": 1500})]
        self.assertEqual(choose_plan(plans, Usage(monthly_volume=200)).plan.name, "Starter")
        self.assertEqual(choose_plan(plans, Usage(monthly_volume=1200)).plan.name, "Pro")

    def selection_setup(self):
        intake = Intake(problem_text=EXAMPLE, monthly_budget=30, usage=Usage(monthly_volume=50))
        comps = self.decompose(EXAMPLE, monthly_budget=30).components
        cands = {c.id: catalog.match(c, intake, self.catalog) for c in comps}
        ids = {c.capability: c.id for c in comps}
        return intake, comps, cands, ids

    def test_every_need_lists_its_tools_with_prices_and_shared_fits(self):
        from agent.options import component_tools
        intake, comps, cands, ids = self.selection_setup()
        tools = component_tools(comps, cands, intake)
        crm = {t["name"]: t for t in tools[ids["crm_contacts"]]}
        self.assertEqual(set(crm), {"HubSpot", "Jobber"})
        self.assertEqual((crm["HubSpot"]["plan_name"], crm["HubSpot"]["monthly_cost"]), ("Free tools", 0.0))
        self.assertIn(ids["form_capture"], crm["HubSpot"]["also_fits"])  # HubSpot also does the form

    def test_owner_selection_is_priced_in_code(self):
        from agent.options import build_selection
        intake, comps, cands, ids = self.selection_setup()
        hub = {ids["form_capture"]: "hubspot", ids["crm_contacts"]: "hubspot"}
        same_tool = build_selection(comps, cands, intake, hub)
        connector = [a for a in same_tool.assignments if a.capability == "workflow_automation"][0]
        self.assertEqual(connector.source, "native")  # both ends in HubSpot: no connector to buy
        self.assertEqual((same_tool.monthly_total, same_tool.total_is_complete), (0.0, True))

        mixed = build_selection(comps, cands, intake, {ids["form_capture"]: "mailchimp", ids["crm_contacts"]: "jobber",
                                                       ids["workflow_automation"]: "zapier"})
        self.assertEqual(mixed.monthly_total, 49.0)  # Mailchimp Free + Jobber Core + Zapier Free

        skipped = build_selection(comps, cands, intake, {ids["form_capture"]: "have", ids["crm_contacts"]: "jobber",
                                                         ids["workflow_automation"]: "skip"})
        self.assertEqual([a.source for a in skipped.assignments], ["existing", "catalog", "skipped"])
        self.assertEqual(skipped.monthly_total, 49.0)

        unpicked = build_selection(comps, cands, intake, {ids["crm_contacts"]: "jobber"})
        self.assertIn("none", [a.source for a in unpicked.assignments])  # a need without a pick can't be saved
        with self.assertRaises(ValueError):
            build_selection(comps, cands, intake, {ids["crm_contacts"]: "web:not-a-candidate.example"})

    def test_purpose_comes_before_price(self):
        from agent.options import component_tools
        intake = Intake(problem_text="Take table reservations", monthly_budget=200, business_type="restaurant")
        need = Component(id="c1", capability="appointment_booking", description="Take table reservations",
                         constraints=[], connects=[], search_query="")
        calendly = catalog.Candidate(tool_id="calendly", name="Calendly", source="catalog", vetted=True, capabilities=[],
                                     plans=[Plan(name="Free", monthly_price=0)], fit="workable",
                                     fit_reason="Appointment scheduler; not made for restaurant tables. Fine for pop-ups.")
        tock = catalog.Candidate(tool_id="web:tock.example", name="Tock", source="web", vetted=False, capabilities=[],
                                 plans=[Plan(name="Base", monthly_price=79)], fit="built_for_this",
                                 fit_reason="Restaurant reservation system.")
        # A tool built for the job exists, so the cheaper "works, with limits" tool is not shown or picked.
        listed = component_tools([need], {"c1": [calendly, tock]}, intake)["c1"]
        self.assertEqual([t["name"] for t in listed], ["Tock"])
        options = build_options([need], {"c1": [calendly, tock]}, intake)
        self.assertEqual(options[0].title, "Best fit")
        self.assertTrue(all(o.assignments[0].tool_name == "Tock" for o in options))
        # With nothing built for the job, the limited tool is shown, with its reason.
        only = component_tools([need], {"c1": [calendly]}, intake)["c1"]
        self.assertEqual([(t["name"], t["fit"]) for t in only], [("Calendly", "workable")])
        self.assertIn("pop-ups", only[0]["fit_reason"])
        # A limited tool the owner already uses stays visible: it costs nothing extra.
        owned = calendly.model_copy(update={"owned": True})
        self.assertEqual({t["name"] for t in component_tools([need], {"c1": [owned, tock]}, intake)["c1"]},
                         {"Calendly", "Tock"})

    def test_tools_not_designed_for_the_job_are_never_the_cheapest_pick(self):
        intake = Intake(problem_text="Take table reservations", monthly_budget=200, business_type="restaurant")
        need = Component(id="c1", capability="appointment_booking", description="Take table reservations",
                         constraints=[], connects=[], search_query="")
        poor = catalog.Candidate(tool_id="a", name="FormTool", source="catalog", vetted=True, capabilities=[],
                                 plans=[Plan(name="Free", monthly_price=0)], fit="poor")
        ok = catalog.Candidate(tool_id="b", name="TableApp", source="web", vetted=False, capabilities=[],
                               plans=[Plan(name="Base", monthly_price=20)], fit="built_for_this")
        for option in build_options([need], {"c1": [poor, ok]}, intake):
            self.assertEqual(option.assignments[0].tool_name, "TableApp")

    def test_unsuitable_tools_are_never_shown_recommended_or_priced(self):
        from agent.options import build_selection, component_tools
        intake = Intake(problem_text="Take table reservations", monthly_budget=200, business_type="restaurant")
        need = Component(id="c1", capability="appointment_booking", description="Take table reservations",
                         constraints=[], connects=[], search_query="")
        poor = catalog.Candidate(tool_id="jobber", name="Jobber", source="catalog", vetted=True, capabilities=[],
                                 plans=[Plan(name="Core", monthly_price=49)], fit="poor",
                                 fit_reason="Home-service software, not for restaurants.")
        limits = catalog.Candidate(tool_id="calendly", name="Calendly", source="catalog", vetted=True, capabilities=[],
                                   plans=[Plan(name="Free", monthly_price=0)], fit="workable")
        cands = {"c1": [poor, limits]}
        self.assertEqual([t["name"] for t in component_tools([need], cands, intake)["c1"]], ["Calendly"])  # kept, qualified
        for option in build_options([need], cands, intake):
            self.assertNotIn("Jobber", [a.tool_name for a in option.assignments])
        with self.assertRaises(ValueError):
            build_selection([need], cands, intake, {"c1": "jobber"})
        # A need with only unsuitable tools gets no option, rather than a bad recommendation.
        self.assertEqual(build_options([need], {"c1": [poor]}, intake), [])

    def test_a_priced_fallback_is_kept_when_specialists_hide_their_prices(self):
        from agent.options import component_tools
        intake = Intake(problem_text="Keep guest records", monthly_budget=100, business_type="restaurant")
        need = Component(id="c1", capability="crm_contacts", description="Keep guest records",
                         constraints=[], connects=[], search_query="")
        unpriced = [catalog.Candidate(tool_id=f"web:{n.lower()}.example", name=n, source="web", vetted=False,
                                      capabilities=[], plans=[Plan(name="Contact sales", monthly_price=None)],
                                      fit="built_for_this") for n in ("EatApp", "SevenRooms")]
        hubspot = catalog.Candidate(tool_id="hubspot", name="HubSpot", source="catalog", vetted=True, capabilities=[],
                                    plans=[Plan(name="Free tools", monthly_price=0)], fit="workable")
        zoho = catalog.Candidate(tool_id="zoho", name="Zoho CRM", source="web", vetted=False, capabilities=[],
                                 plans=[Plan(name="Standard", monthly_price=14)], fit="workable")
        shown = [t["name"] for t in component_tools([need], {"c1": [*unpriced, hubspot, zoho]}, intake)["c1"]]
        self.assertEqual(set(shown), {"EatApp", "SevenRooms", "HubSpot"})  # cheapest priced limited tool only
        # Once any specialist lists a price, the limited fallback is hidden again.
        priced = unpriced[0].model_copy(update={"plans": [Plan(name="Pro", monthly_price=89)]})
        shown = [t["name"] for t in component_tools([need], {"c1": [priced, unpriced[1], hubspot]}, intake)["c1"]]
        self.assertNotIn("HubSpot", shown)

    def test_owned_tool_wins_cost_ties(self):
        intake = Intake(problem_text=EXAMPLE, monthly_budget=30, existing_software=["Wix"])
        comps = self.decompose(EXAMPLE, monthly_budget=30).components
        cands = {c.id: [x for x in catalog.match(c, intake, self.catalog) if x.tool_id != "hubspot"] for c in comps}
        form = [a for a in build_options(comps, cands, intake)[0].assignments if a.capability == "form_capture"][0]
        self.assertEqual((form.tool_id, form.source, form.monthly_cost), ("wix", "owned", 0.0))


if __name__ == "__main__":
    unittest.main()
