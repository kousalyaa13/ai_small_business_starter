import json
import os
import unittest
from unittest import mock

from agent.decompose import DECOMPOSE_INSTRUCTIONS, apply_answers, clean_decomposition, intake_prompt
from agent.llm import OpenRouterModel, StubModel, default_model
from agent.schemas import ClarifyingQuestion, Component, Decomposition, Intake

EXAMPLE = ("Whenever someone fills out our contact form, add them to our CRM without creating duplicate "
           "contacts. Keep additional software costs under $30 per month.")


def stub_decompose(text, **intake):
    result, _ = StubModel().structured("decompose", "", intake_prompt(Intake(problem_text=text, **intake), 0),
                                       Decomposition)
    return result


class DecompositionTests(unittest.TestCase):
    def test_fiscal_budget_intake_extracts_explicit_numbers_without_asking_editors(self):
        request = ("Need to manage my budget development and tracking every fiscal year. "
                   "Budget of 75 per month. 5 people need access.")
        intake = Intake(problem_text=request)
        self.assertEqual((intake.monthly_budget, intake.usage.seats), (75, 5))
        result = stub_decompose(request)
        caps = {c.capability for c in result.components}
        self.assertTrue({"budget_planning", "budget_tracking"} <= caps)
        components, questions = clean_decomposition(result, intake)
        self.assertEqual(questions, [])  # editor counts only change price; owners adjust them after seeing options

    def test_contact_form_example(self):
        result = stub_decompose(EXAMPLE, monthly_budget=30)
        caps = {c.capability: c for c in result.components}
        self.assertTrue({"form_capture", "crm_contacts", "workflow_automation"} <= set(caps))
        self.assertIn("Avoid duplicate records (match on email)", caps["crm_contacts"].constraints)
        self.assertEqual(len(caps["workflow_automation"].connects), 2)
        self.assertNotIn("monthly_budget", [q.field for q in result.questions])

    def test_instructions_limit_noise(self):
        for rule in ["15 words or fewer", "at most 3 per component", "Do not invent",
                     "Never put budget, price, seats or volume in constraints", "match on email"]:
            self.assertIn(rule, DECOMPOSE_INSTRUCTIONS)
        self.assertNotIn("under $30/month", DECOMPOSE_INSTRUCTIONS)
        for rule in ["search_query", "Do not add extras", "including the business type", "main job",
                     'is "other", not "appointment_booking"']:
            self.assertIn(rule, DECOMPOSE_INSTRUCTIONS)

    def test_business_type_reaches_the_prompt_and_queries_are_filled(self):
        intake = Intake(problem_text=EXAMPLE, monthly_budget=30, business_type="plumbing")
        self.assertIn("Business type: plumbing", intake_prompt(intake, 0))
        result = Decomposition(summary="s", questions=[], components=[
            Component(id="c1", capability="crm_contacts", description="Store leads", connects=[], constraints=[],
                      search_query="  ")])
        components, _ = clean_decomposition(result, intake)
        self.assertEqual(components[0].search_query, "Store leads")  # never left empty

    def test_constraints_are_capped_and_connects_cleaned(self):
        noisy = Decomposition(summary="s", questions=[], components=[
            Component(id="c1", capability="crm_contacts", description="CRM", connects=["c1", "c9"],
                      constraints=["a", "b", "c", "d", "e"], search_query="crm for small business")])
        components, _ = clean_decomposition(noisy, Intake(problem_text=EXAMPLE, monthly_budget=30))
        self.assertEqual(components[0].constraints, ["a", "b", "c"])
        self.assertEqual(components[0].connects, [])

    def test_budget_question_is_always_asked_when_missing(self):
        result = Decomposition(summary="s", questions=[], components=[
            Component(id="c1", capability="crm_contacts", description="CRM", connects=[], constraints=[],
                      search_query="crm for small business")])
        _, questions = clean_decomposition(result, Intake(problem_text=EXAMPLE))
        self.assertEqual(questions[0].field, "monthly_budget")

    def test_questions_about_known_fields_are_dropped(self):
        result = Decomposition(summary="s", components=[], questions=[
            ClarifyingQuestion(id="q1", field="monthly_budget", question="Budget?"),
            ClarifyingQuestion(id="q2", field="text", question="Where is your form hosted?")])
        _, questions = clean_decomposition(result, Intake(problem_text=EXAMPLE, monthly_budget=30))
        self.assertEqual([q.id for q in questions], ["q2"])

    def test_volume_and_seat_questions_are_not_asked_up_front(self):
        result = Decomposition(summary="s", components=[], questions=[
            ClarifyingQuestion(id="q1", field="monthly_volume", question="How many reservations a month?"),
            ClarifyingQuestion(id="q2", field="editors", question="How many admins?"),
            ClarifyingQuestion(id="q3", field="text", question="Where does your booking page live?"),
            ClarifyingQuestion(id="q4", field="text", question="Do you take deposits?")])
        _, questions = clean_decomposition(result, Intake(problem_text=EXAMPLE, monthly_budget=200))
        self.assertEqual([q.id for q in questions], ["q3"])  # one question about which kind of tool, nothing else

    def test_apply_answers(self):
        questions = [{"id": "q_budget", "field": "monthly_budget", "question": "Budget?"},
                     {"id": "q_volume", "field": "monthly_volume", "question": "Volume?"},
                     {"id": "q_where", "field": "text", "question": "Where is your form hosted?"}]
        intake = apply_answers(Intake(problem_text=EXAMPLE), questions,
                               {"q_budget": "$30", "q_volume": "1,200", "q_where": "Wix"})
        self.assertEqual((intake.monthly_budget, intake.usage.monthly_volume), (30.0, 1200))
        self.assertIn("Where is your form hosted? Wix", intake.requirements)


class EditorAnswerTests(unittest.TestCase):
    def test_more_editors_than_team_size_raises_team_size(self):
        q = [{"id": "q1", "field": "editors", "question": "How many people need admin access?"}]
        intake = apply_answers(Intake(problem_text=EXAMPLE), q, {"q1": "3"})  # team size was never given
        self.assertEqual((intake.usage.seats, intake.usage.editors), (3, 3))


class ProviderSelectionTests(unittest.TestCase):
    def test_forced_provider_wins(self):
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "x", "OPENROUTER_API_KEY": "y",
                                          "AGENT_PROVIDER": "openrouter"}):
            self.assertEqual(default_model().name, "openrouter")
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "", "OPENROUTER_API_KEY": "", "AGENT_PROVIDER": ""}):
            self.assertEqual(default_model().name, "stub")


class OpenRouterAdapterTests(unittest.TestCase):
    def test_parses_structured_output_and_usage(self):
        import httpx
        from openai import OpenAI
        seen = {}
        decomposition = {"summary": "Two parts.", "questions": [], "components": [
            {"id": "c1", "capability": "form_capture", "description": "Contact form", "constraints": [], "connects": [],
             "search_query": "contact form builder"}]}

        def handler(request):
            seen["url"], seen["body"] = str(request.url), json.loads(request.content)
            return httpx.Response(200, json={
                "id": "gen-1", "object": "chat.completion", "created": 0, "model": seen["body"]["model"],
                "choices": [{"index": 0, "finish_reason": "stop",
                             "message": {"role": "assistant", "content": json.dumps(decomposition)}}],
                "usage": {"prompt_tokens": 120, "completion_tokens": 30, "total_tokens": 150,
                          "prompt_tokens_details": {"cached_tokens": 100}}})

        client = OpenAI(api_key="test", base_url=OpenRouterModel.base_url,
                        http_client=httpx.Client(transport=httpx.MockTransport(handler)))
        result, usage = OpenRouterModel(client).structured("decompose", "instructions", "Problem: x", Decomposition)
        self.assertTrue(seen["url"].startswith("https://openrouter.ai/api/v1/chat/completions"))
        self.assertEqual(seen["body"]["response_format"]["type"], "json_schema")
        self.assertEqual(seen["body"]["provider"], {"require_parameters": True})
        self.assertNotIn("reasoning", seen["body"])  # decomposition keeps the model's default effort
        OpenRouterModel(client).structured("extract", "instructions", "Page text: x", Decomposition)
        self.assertEqual(seen["body"]["reasoning"], {"effort": "low"})
        self.assertEqual(result.components[0].capability, "form_capture")
        self.assertEqual((usage.provider, usage.input_tokens, usage.cached_input_tokens, usage.output_tokens),
                         ("openrouter", 120, 100, 30))


if __name__ == "__main__":
    unittest.main()
