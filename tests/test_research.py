import socket
import tempfile
import unittest
from pathlib import Path

from agent import catalog
from agent.budget import Limits, Meter
from agent.llm import StubModel
from agent.research import Researcher, SearchResult, price_in_quote, quote_on_page
from agent.safe_fetch import FetchError, SafeFetcher, check_url, page_from_html
from agent.schemas import Component, ExtractedPlan, ExtractedTool, Intake
from agent.store import Store

EXAMPLE = ("Whenever someone fills out our contact form, add them to our CRM without creating duplicate "
           "contacts. Keep additional software costs under $30 per month.")


def public_resolver(host, port, type=0):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]


class FakeSearch:
    provider = "fake"

    def __init__(self, results):
        self.results, self.calls = results, 0

    def search(self, query, count=8):
        self.calls += 1
        return self.results


class FakeFetcher:
    def __init__(self, pages):
        self.pages, self.calls = pages, []

    def fetch(self, url):
        self.calls.append(url)
        if url not in self.pages:
            raise FetchError("not found")
        return page_from_html(url, self.pages[url])


class FixedExtraction(StubModel):
    """Stub decomposition, but a scripted extraction result (to simulate model mistakes)."""

    def __init__(self, extraction):
        self.extraction = extraction

    def structured(self, task, instructions, content, schema):
        if schema is ExtractedTool:
            result, usage = super().structured(task, instructions, content, schema)
            return self.extraction, usage
        return super().structured(task, instructions, content, schema)


class TinyFishClientTests(unittest.TestCase):
    class Resp:
        def __init__(self, status, body):
            self.status_code, self.body = status, body

        def json(self):
            return self.body

        def raise_for_status(self):
            if self.status_code >= 400:
                import requests
                raise requests.HTTPError(str(self.status_code))

    class Session:
        def __init__(self, resp):
            self.resp, self.calls = resp, []

        def get(self, url, **kw):
            self.calls.append(("GET", url, kw))
            return self.resp

        def post(self, url, **kw):
            self.calls.append(("POST", url, kw))
            return self.resp

    def test_search_maps_results_and_sends_key_header(self):
        from agent.research import TinyFishSearch
        session = self.Session(self.Resp(200, {"query": "q", "results": [
            {"position": 1, "site_name": "leadforms.example", "title": "LeadForms", "url": "https://leadforms.example/",
             "snippet": "Forms"}]}))
        results = TinyFishSearch("tf-key", session=session).search("contact form tool")
        self.assertEqual((results[0].title, results[0].url), ("LeadForms", "https://leadforms.example/"))
        method, url, kw = session.calls[0]
        self.assertEqual((method, url), ("GET", "https://api.search.tinyfish.ai/"))
        self.assertEqual(kw["headers"]["X-API-Key"], "tf-key")
        self.assertIn("g2.com", kw["params"]["exclude_domains"])

    def test_fetch_returns_page_text(self):
        from agent.research import TinyFishFetcher
        session = self.Session(self.Resp(200, {"results": [
            {"url": "https://leadforms.example/pricing", "final_url": "https://leadforms.example/pricing",
             "title": "Pricing", "text": "# Pricing\n\nStarter plan: $12/month"}], "errors": []}))
        page = TinyFishFetcher("tf-key", session=session).fetch("https://leadforms.example/pricing")
        self.assertIn("Starter plan: $12/month", page.text)
        self.assertEqual(session.calls[0][2]["json"]["urls"], ["https://leadforms.example/pricing"])

    def test_fetch_errors_become_fetch_errors(self):
        from agent.research import TinyFishFetcher
        blocked = self.Session(self.Resp(200, {"results": [], "errors": [{"url": "u", "error": "bot_blocked"}]}))
        with self.assertRaisesRegex(FetchError, "blocked"):
            TinyFishFetcher("k", session=blocked).fetch("https://vendor.example/")
        with self.assertRaisesRegex(FetchError, "credits"):
            TinyFishFetcher("k", session=self.Session(self.Resp(402, {}))).fetch("https://vendor.example/")
        with self.assertRaises(FetchError):
            TinyFishFetcher("k", session=blocked).fetch("http://vendor.example/")

    def test_tinyfish_preferred_when_configured(self):
        import os
        from unittest import mock
        from agent.research import TinyFishFetcher, TinyFishSearch, default_fetcher, default_search
        with mock.patch.dict(os.environ, {"TINYFISH_API_KEY": "k", "BRAVE_API_KEY": "b"}):
            self.assertIsInstance(default_search(), TinyFishSearch)
            self.assertIsInstance(default_fetcher(), TinyFishFetcher)
        with mock.patch.dict(os.environ, {"TINYFISH_API_KEY": "", "BRAVE_API_KEY": ""}):
            self.assertIsNone(default_search())
            self.assertIsInstance(default_fetcher(), SafeFetcher)


class SafeFetchTests(unittest.TestCase):
    def test_rejects_unsafe_urls(self):
        for url in ["http://example.com", "https://user@example.com", "https://example.com:8443/"]:
            with self.assertRaises(FetchError):
                check_url(url, resolver=public_resolver)
        for ip in ["127.0.0.1", "10.0.0.5", "169.254.169.254", "192.168.1.1"]:
            with self.assertRaises(FetchError):
                check_url("https://vendor.example/", resolver=lambda h, p, type=0, ip=ip: [(2, 1, 6, "", (ip, p))])
        self.assertEqual(check_url("https://vendor.example/", resolver=public_resolver), "vendor.example")

    def test_redirect_to_internal_host_is_blocked(self):
        class Resp:
            def __init__(self, status, headers, body=b""):
                self.status_code, self.headers, self.body = status, headers, body

            def iter_content(self, n):
                yield self.body

            def close(self):
                pass

        class Session:
            def get(self, url, **kw):
                return Resp(302, {"location": "https://internal.example/admin"})

        def resolver(host, port, type=0):
            ip = "10.1.2.3" if host == "internal.example" else "93.184.216.34"
            return [(2, 1, 6, "", (ip, port))]

        fetcher = SafeFetcher(session=Session(), resolver=resolver, respect_robots=False)
        with self.assertRaises(FetchError):
            fetcher.fetch("https://vendor.example/")


class ResearchTests(unittest.TestCase):
    PAGE = ("<html><head><title>LeadForms - Simple forms</title></head><body><h1>LeadForms</h1>"
            "<p>LeadForms lets you build a contact form and collect every submission in one place.</p>"
            "<p>Starter plan: $12/month for unlimited forms.</p></body></html>")

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "app.db")
        self.project_id, self.run_id = self.store.create_project(Intake(problem_text=EXAMPLE).model_dump())
        self.component = Component(id="c1", capability="form_capture", description="Collect form submissions",
                                   constraints=[], connects=[])

    def tearDown(self):
        self.tmp.cleanup()

    def researcher(self, model, limits=None, results=None):
        search = FakeSearch(results or [SearchResult("G2 list", "https://www.g2.com/best-forms", ""),
                                        SearchResult("LeadForms", "https://leadforms.example/", "")])
        fetcher = FakeFetcher({"https://leadforms.example/": self.PAGE})
        meter = Meter(self.store, self.run_id, self.project_id, limits or Limits())
        return Researcher(self.store, meter, model, search, fetcher, catalog.load_catalog()), search, fetcher

    def test_verified_price_is_kept_and_review_sites_skipped(self):
        researcher, _, fetcher = self.researcher(StubModel())
        found, _ = researcher.research(self.component, Intake(problem_text=EXAMPLE))
        self.assertEqual(len(found), 1)
        cand = found[0]
        self.assertEqual((cand.source, cand.vetted), ("web", False))
        self.assertEqual(cand.plans[0].monthly_price, 12.0)
        self.assertFalse(any("g2.com" in u for u in fetcher.calls))

    def test_fabricated_price_is_dropped(self):
        fabricated = ExtractedTool(
            is_vendor_product_page=True, name="LeadForms", capabilities=["form_capture"],
            description_quote="LeadForms lets you build a contact form and collect every submission in one place.",
            plans=[ExtractedPlan(name="Starter", monthly_price=5, per_seat=False, billing="monthly",
                                 price_quote="Starter plan: $5/month")],
            api_available=True, api_quote="Full REST API")
        researcher, _, _ = self.researcher(FixedExtraction(fabricated))
        found, _ = researcher.research(self.component, Intake(problem_text=EXAMPLE))
        self.assertEqual(found[0].plans, [])
        self.assertTrue(any("dropped" in n for n in found[0].notes))

    def test_unverifiable_description_discards_candidate(self):
        invented = ExtractedTool(is_vendor_product_page=True, name="LeadForms", capabilities=["form_capture"],
                                 description_quote="The #1 AI form builder trusted by millions.",
                                 plans=[], api_available=None, api_quote="")
        researcher, _, _ = self.researcher(FixedExtraction(invented))
        found, notes = researcher.research(self.component, Intake(problem_text=EXAMPLE))
        self.assertEqual(found, [])
        self.assertTrue(any("could not be verified" in n for n in notes))

    def test_limits_stop_research(self):
        researcher, search, _ = self.researcher(StubModel(), limits=Limits(max_searches=0))
        found, notes = researcher.research(self.component, Intake(problem_text=EXAMPLE))
        self.assertEqual((found, search.calls), ([], 0))
        self.assertTrue(any("search limit reached" in n for n in notes))

    def test_article_urls_are_skipped(self):
        from agent.research import looks_like_article
        self.assertTrue(looks_like_article("https://www.pandadoc.com/blog/form-builder-apps/"))
        self.assertTrue(looks_like_article("https://albato.com/blog/publications/best-form-builder-software"))
        self.assertFalse(looks_like_article("https://www.jotform.com/pricing/"))
        researcher, _, fetcher = self.researcher(StubModel(), results=[
            SearchResult("Best forms", "https://vendor-blog.example/blog/best-forms", ""),
            SearchResult("LeadForms", "https://leadforms.example/", "")])
        found, _ = researcher.research(self.component, Intake(problem_text=EXAMPLE))
        self.assertEqual([c.name for c in found], ["LeadForms"])
        self.assertFalse(any("vendor-blog" in u for u in fetcher.calls))

    def test_quote_helpers(self):
        self.assertTrue(quote_on_page("Starter  plan: $12/month", "x Starter plan: $12/month y"))
        self.assertTrue(price_in_quote(12, "$12/month"))
        self.assertFalse(price_in_quote(12, "$120/month"))
        self.assertTrue(price_in_quote(0, "Free forever"))


if __name__ == "__main__":
    unittest.main()
