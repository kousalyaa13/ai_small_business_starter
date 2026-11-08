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
        for expected in ("consultation-card", "needs-details", "options",
                         "adjust-toggle", "recovery-panel", "pricing-questions",
                         "approve-line", "build-budget", "selection-note"):
            self.assertIn(expected, ids)
        # The compare tab was removed: it repeated what the needs picker already shows.
        self.assertNotIn("compare-view", ids)
        self.assertNotIn("compare-toggle", ids)
        self.assertNotIn("research-all", ids)  # searches run per need, never for everything at once

    def test_results_load_before_recovery_wrapper(self):
        html = (ROOT / "templates" / "consult.html").read_text()
        self.assertLess(html.index("consult-results.js"), html.index("consult-recovery.js"))
        self.assertIn("consult-results.css", html)

    def test_needs_picker_lists_tools_per_need(self):
        js = (ROOT / "static" / "consult-results.js").read_text()
        self.assertIn("Visit website", js)
        self.assertIn("data-need-row", js)        # one row per need
        self.assertIn("I already have this", js)
        self.assertIn("Skip for now", js)
        self.assertIn("/selection", js)           # prices come from the server
        self.assertIn("openNeeds", js)            # open rows survive redraws
        self.assertIn("groupTools", js)
        self.assertNotIn("compareTable", js)
        # A search keeps the owner in control and reports what it found.
        self.assertIn("Search finished", js)
        self.assertIn("one search runs at a time", js)
        self.assertIn("You can save once the search", js)
        self.assertIn("Cheaper than your pick", js)
        # Fit for the job is shown first, and "Reviewed" is about the listing, not suitability.
        for text in ("Built for this", "Works, with limits", "Not designed for this", "Reviewed listing", "Before you save"):
            self.assertIn(text, js)
        self.assertNotIn("other tool fit this part", js.lower())
        html = (ROOT / "templates" / "consult.html").read_text()
        self.assertNotIn("altList(", html)
        self.assertNotIn("other tool fit this part", html.lower())


if __name__ == "__main__":
    unittest.main()
