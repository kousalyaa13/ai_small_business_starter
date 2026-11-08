"""Smoke checks for the consultation's product-first HTML integration."""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class ConsultationLayoutTests(unittest.TestCase):
    def test_unique_ids_and_core_controls(self):
        html = (ROOT / "templates" / "consult.html").read_text()
        ids = re.findall(r'\bid="([^"]+)"', html)
        self.assertEqual(len(ids), len(set(ids)), "Duplicate HTML IDs")
        for expected in ("consultation-card", "needs-details", "options", "compare-view",
                         "compare-toggle", "adjust-toggle", "recovery-panel",
                         "approve-line", "build-budget", "selection-note"):
            self.assertIn(expected, ids)

    def test_results_load_before_recovery_wrapper(self):
        html = (ROOT / "templates" / "consult.html").read_text()
        self.assertLess(html.index("consult-results.js"), html.index("consult-recovery.js"))
        self.assertIn("consult-results.css", html)

    def test_product_detail_links_are_distinct(self):
        js = (ROOT / "static" / "consult-results.js").read_text()
        self.assertIn("Visit website", js)
        self.assertIn("pricingLinks", js)
        self.assertIn("groupTools", js)
        self.assertNotIn("other tool fit this part", js.lower())


if __name__ == "__main__":
    unittest.main()
