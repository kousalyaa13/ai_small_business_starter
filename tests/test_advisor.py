import unittest
from advisor import assess, audit, blueprint, reviews_for

class AdvisorTests(unittest.TestCase):
    def setUp(self):
        self.profile = {
            "industry":"coffee","problem":"retention","software":"Square",
            "budget":"75","comfort":"beginner","data_available":"yes","consent":"yes"
        }
    def test_readiness_when_data_and_opt_in_exist(self):
        result = assess(self.profile, "retention")
        self.assertEqual(result["score"], 90)
        self.assertEqual(result["blockers"], [])
    def test_marketing_consent_is_a_blocker(self):
        result = assess({**self.profile,"consent":"unsure"}, "retention")
        self.assertTrue(any("consent" in item.lower() for item in result["blockers"]))
    def test_skipped_questions_are_not_blockers(self):
        result = assess({"software":"Square","budget":"75"}, "retention")
        self.assertEqual(result["blockers"], [])
        self.assertEqual(set(result["unanswered"]), {"data_available","consent"})
    def test_existing_software_needs_verification(self):
        value = audit(self.profile, "retention")
        self.assertIn("Square", value["software"])
        self.assertIn("Check", value["try_first"])
    def test_all_workflows_have_explanations(self):
        for problem in ("retention","marketing","finance","website","support","scheduling","admin"):
            result = blueprint(self.profile, problem)
            self.assertEqual(len(result["nodes"]), 5)
            self.assertEqual(len(result["before"]), 4)
            self.assertEqual(len(result["after"]), 4)
    def test_no_fabricated_review(self):
        review = reviews_for({"name":"Square Marketing"})
        self.assertIn("g2.com", review["search_url"])
        self.assertNotIn("rating", review)

if __name__ == "__main__":
    unittest.main()
