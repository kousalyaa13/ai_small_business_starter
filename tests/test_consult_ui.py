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
                         "fit-card", "recovery-panel",
                         "approve-line", "build-budget", "selection-note"):
            self.assertIn(expected, ids)
        # The compare tab was removed: it repeated what the needs picker already shows.
        self.assertNotIn("compare-view", ids)
        self.assertNotIn("compare-toggle", ids)
        self.assertNotIn("research-all", ids)  # searches run per need, never for everything at once
        # After the owner chooses: the connection check and the setup plan.
        self.assertIn("connection-check", ids)
        self.assertIn("setup-plan", ids)
        self.assertLess(html.index("consult-results.js"), html.index("consult-setup.js"))
        setup_js = (ROOT / "static" / "consult-setup.js").read_text()
        self.assertIn("Build my setup guide", setup_js)  # a saved draft leads to the setup guide
        self.assertIn("/approve", setup_js)
        # The guide no longer depends on a browser draft (Save now downloads a PDF).
        self.assertNotIn("bloom-saved-plan", setup_js)
        self.assertIn("allChosen", setup_js)
        # Pricing-only questions (contacts, automated tasks, seats) were removed: they never change which tools fit.
        self.assertNotIn("pricing-questions", ids)

    def test_results_load_before_recovery_wrapper(self):
        html = (ROOT / "templates" / "consult.html").read_text()
        self.assertLess(html.index("consult-results.js"), html.index("consult-recovery.js"))
        self.assertIn("consult-results.css", html)

    def test_needs_picker_lists_tools_per_need(self):
        js = (ROOT / "static" / "consult-results.js").read_text()
        self.assertIn("Visit website", js)
        self.assertIn("data-tab", js)             # a horizontal strip with one tab per need
        self.assertIn("carousel-track", js)       # tools for the open need, browsed like a carousel
        self.assertIn("I already have this", js)
        self.assertIn("Skip for now", js)
        self.assertIn("/selection", js)           # prices come from the server
        self.assertIn("trackScroll", js)          # carousel position survives redraws
        self.assertIn("plain_note", js)           # owners see one plain line per need, not working notes
        # Show only the top five matching tools, without an expand-all toggle.
        self.assertIn("TOP_TOOLS = 5", js)
        self.assertIn("all.slice(0, TOP_TOOLS)", js)
        self.assertNotIn("Show all ", js)
        self.assertIn("groupTools", js)
        self.assertNotIn("compareTable", js)
        # A search keeps the owner in control and reports what it found.
        self.assertIn("Search finished", js)
        self.assertIn("one search runs at a time", js)
        self.assertIn("You can save once the search", js)
        self.assertIn("Cheaper than your pick", js)
        # Fit for the job is shown first, and "Reviewed" is about the listing, not suitability.
        for text in ("Built for this", "Works, with limits", "Not designed for this", "Reviewed listing"):
            self.assertIn(text, js)
        self.assertNotIn("other tool fit this part", js.lower())
        html = (ROOT / "templates" / "consult.html").read_text()
        self.assertNotIn("altList(", html)
        self.assertNotIn("other tool fit this part", html.lower())


if __name__ == "__main__":
    unittest.main()
