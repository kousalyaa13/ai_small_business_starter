"""Web research for components the in-house catalog cannot cover.

Search finds candidate vendor pages, a cheap model extracts structured facts, and
application code keeps only facts whose quoted evidence appears on the fetched page.
Web candidates are always unvetted.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlparse

import requests

from .budget import BudgetExceeded, Meter
from .capabilities import CAPABILITIES
from .safe_fetch import FetchError, Page, SafeFetcher
from .schemas import Candidate, Component, Evidence, ExtractedTool, Intake, Plan

# Review sites, forums and media are not vendor pages; we want official sources.
SKIP_DOMAINS = {
    "g2.com", "capterra.com", "getapp.com", "softwareadvice.com", "trustradius.com", "reddit.com",
    "quora.com", "youtube.com", "medium.com", "forbes.com", "techradar.com", "pcmag.com", "zdnet.com",
    "wikipedia.org", "linkedin.com", "facebook.com", "x.com", "twitter.com", "producthunt.com",
    "nerdwallet.com", "businessnewsdaily.com", "investopedia.com", "github.com",
}

EXTRACT_INSTRUCTIONS = """You extract facts about a software product from the text of its own website.
Rules:
- Only use the provided page text. Never use outside knowledge.
- is_vendor_product_page is true only if the page is the official site of a software product (not a review, list or blog).
- capabilities: choose only from the allowed tags that the page text explicitly supports.
- description_quote: copy one sentence verbatim from the page text describing what the product does.
- plans: for each plan whose monthly price is stated, copy the exact text stating the price into price_quote.
  If no price is stated, set monthly_price to null. Use billing "annual" if the price is only for yearly billing.
- api_quote: copy verbatim text mentioning an API or developer platform, or "" if none.
Allowed capability tags: """ + ", ".join(CAPABILITIES)


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str


class SearchClient(Protocol):
    provider: str

    def search(self, query: str, count: int = 8) -> list[SearchResult]:
        ...


class BraveSearch:
    provider = "brave"
    endpoint = "https://api.search.brave.com/res/v1/web/search"

    def __init__(self, api_key, session=None, timeout=10):
        self.api_key, self.session, self.timeout = api_key, session or requests.Session(), timeout

    def search(self, query, count=8):
        resp = self.session.get(self.endpoint, params={"q": query, "count": count, "country": "us"},
                                headers={"X-Subscription-Token": self.api_key, "Accept": "application/json"},
                                timeout=self.timeout)
        resp.raise_for_status()
        results = resp.json().get("web", {}).get("results", [])
        return [SearchResult(r.get("title", ""), r.get("url", ""), r.get("description", "")) for r in results]


class TinyFishSearch:
    """TinyFish Search API: GET https://api.search.tinyfish.ai/?query=..."""

    provider = "tinyfish"
    endpoint = "https://api.search.tinyfish.ai/"

    def __init__(self, api_key, session=None, timeout=20):
        self.api_key, self.session, self.timeout = api_key, session or requests.Session(), timeout

    def search(self, query, count=8):
        resp = self.session.get(self.endpoint, timeout=self.timeout,
                                headers={"X-API-Key": self.api_key, "Accept": "application/json"},
                                params={"query": query, "location": "US", "language": "en",
                                        "exclude_domains": ",".join(sorted(SKIP_DOMAINS)),
                                        "purpose": "Find official vendor websites and pricing pages for "
                                                   "small-business software"})
        resp.raise_for_status()
        results = resp.json().get("results", [])[:count]
        return [SearchResult(r.get("title", ""), r.get("url", ""), r.get("snippet", "")) for r in results]


TINYFISH_FETCH_ERRORS = {
    "page_not_found": "The page was not found.",
    "bot_blocked": "The site blocked automated reading.",
    "login_required": "The page requires a login.",
    "timeout": "The page took too long to load.",
    "empty_content": "The page had no readable text.",
}


class TinyFishFetcher:
    """TinyFish Fetch API: renders pages in a browser on TinyFish's servers, so prices
    loaded by JavaScript are readable. Returns the same Page shape as SafeFetcher."""

    provider = "tinyfish"
    endpoint = "https://api.fetch.tinyfish.ai/"

    def __init__(self, api_key, session=None, timeout=45, per_url_timeout_ms=30_000):
        self.api_key, self.session = api_key, session or requests.Session()
        self.timeout, self.per_url_timeout_ms = timeout, per_url_timeout_ms

    def fetch(self, url, allowed_domains=None):
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise FetchError("Only standard HTTPS links can be fetched.")
        if allowed_domains is not None and not any(
                parsed.hostname == d or parsed.hostname.endswith("." + d) for d in allowed_domains):
            raise FetchError("This domain is not on the approved list.")
        try:
            resp = self.session.post(self.endpoint, timeout=self.timeout,
                                     headers={"X-API-Key": self.api_key, "Content-Type": "application/json"},
                                     json={"urls": [url], "format": "markdown",
                                           "per_url_timeout_ms": self.per_url_timeout_ms,
                                           "purpose": "Read a software vendor's product and pricing page"})
        except requests.RequestException:
            raise FetchError("The page reader service couldn't be reached.")
        if resp.status_code in (401, 402, 429):
            raise FetchError({401: "The TinyFish API key was rejected.",
                              402: "TinyFish credits are used up.",
                              429: "TinyFish rate limit reached; try again shortly."}[resp.status_code])
        if resp.status_code >= 400:
            raise FetchError("The page reader service returned an error.")
        body = resp.json()
        results = [r for r in body.get("results", []) if isinstance(r.get("text"), str) and r["text"].strip()]
        if not results:
            code = (body.get("errors") or [{}])[0].get("error", "")
            raise FetchError(TINYFISH_FETCH_ERRORS.get(code, "The page could not be read."))
        r = results[0]
        text = " ".join(r["text"].split())
        return Page(url=r.get("final_url") or url, title=(r.get("title") or "")[:160],
                    description=(r.get("description") or "")[:800], headings="", text=text)


def default_search() -> SearchClient | None:
    """TinyFish if configured, otherwise Brave, otherwise no web research."""
    if os.environ.get("TINYFISH_API_KEY"):
        return TinyFishSearch(os.environ["TINYFISH_API_KEY"])
    if os.environ.get("BRAVE_API_KEY"):
        return BraveSearch(os.environ["BRAVE_API_KEY"])
    return None


def default_fetcher():
    """Browser-rendered fetching through TinyFish if configured, else direct fetching."""
    if os.environ.get("TINYFISH_API_KEY"):
        return TinyFishFetcher(os.environ["TINYFISH_API_KEY"])
    return SafeFetcher()


# Listicles and articles ("best form builders 2026") are not vendor product pages.
ARTICLE_PATH = re.compile(r"/(blog|blogs|article|articles|guide|guides|news|publications|resources|learn)(/|$)|/best-", re.I)


def looks_like_article(url):
    return bool(ARTICLE_PATH.search(urlparse(url).path or ""))


def _norm(text):
    return " ".join(re.sub(r"[‘’]", "'", re.sub(r"[“”]", '"', text or "")).lower().split())


def quote_on_page(quote, page_text) -> bool:
    quote = _norm(quote)
    return len(quote) >= 4 and quote in _norm(page_text)


def price_in_quote(price, quote) -> bool:
    if price == 0:
        return bool(re.search(r"\bfree\b|\$\s?0(\.00)?\b", quote, re.I))
    whole, _, cents = f"{price:.2f}".partition(".")
    pattern = rf"{whole}(\.{cents})?" if cents == "00" else rf"{whole}\.{cents}"
    return bool(re.search(rf"(?<![\d.]){pattern}(?![\d])", quote))


def base_host(url):
    host = (urlparse(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


class Researcher:
    def __init__(self, store, meter: Meter, model, search: SearchClient | None, fetcher, catalog,
                 candidates_per_component=3):
        self.store, self.meter, self.model, self.search, self.fetcher = store, meter, model, search, fetcher
        self.candidates_per_component = candidates_per_component
        self.catalog_hosts = {base_host(t.get("site", "")) for t in catalog}

    def queries(self, component: Component, intake: Intake):
        need = CAPABILITIES.get(component.capability, component.description)
        if component.capability == "other":
            need = component.description
        return [f"AI software tool to {need.lower()} for small business pricing"]

    def _search(self, query):
        cached = self.store.cache_get(self.meter.run_id, "search", query)
        if cached is not None:
            return [SearchResult(**r) for r in cached]
        self.meter.ensure("search")
        results = self.search.search(query)
        self.meter.record_search(self.search.provider)
        self.store.cache_put(self.meter.run_id, "search", query, [r.__dict__ for r in results])
        return results

    def _page(self, url):
        cached = self.store.cache_get(self.meter.run_id, "page", url)
        if cached is not None:
            if "error" in cached:
                raise FetchError(cached["error"])
            return cached
        self.meter.ensure("fetch")
        self.meter.record_fetch(getattr(self.fetcher, "provider", "direct"))
        try:
            page = self.fetcher.fetch(url)
        except FetchError as exc:
            self.store.cache_put(self.meter.run_id, "page", url, {"error": str(exc)})
            raise
        data = {"url": page.url, "title": page.title, "text": page.text[:20_000]}
        self.store.cache_put(self.meter.run_id, "page", url, data)
        return data

    def research(self, component: Component, intake: Intake) -> tuple[list[Candidate], list[str]]:
        label = component.capability.replace("_", " ")
        if self.search is None:
            return [], [f"Web research unavailable for '{label}': no search API key is configured."]
        notes, candidates, seen = [], [], set()
        try:
            for query in self.queries(component, intake):
                for result in self._search(query):
                    host = base_host(result.url)
                    if (not host or host in seen or host in self.catalog_hosts or looks_like_article(result.url)
                            or any(host == d or host.endswith("." + d) for d in SKIP_DOMAINS)):
                        continue
                    seen.add(host)
                    candidate, note = self._evaluate(component, result)
                    if note:
                        notes.append(note)
                    if candidate:
                        candidates.append(candidate)
                    if len(candidates) >= self.candidates_per_component:
                        break
        except BudgetExceeded as exc:
            notes.append(f"Research for '{label}' stopped early: {exc}.")
        except requests.RequestException:
            notes.append(f"Web search for '{label}' failed; try again later.")
        if not candidates:
            notes.append(f"No tool with verifiable evidence was found on the web for '{label}'.")
        return candidates, notes

    def _evaluate(self, component, result):
        host = base_host(result.url)
        try:
            page = self._page(result.url)
        except FetchError as exc:
            return None, f"Skipped {host}: {exc}"
        pages = [page]
        pricing_url = f"https://{urlparse(page['url']).hostname}/pricing"
        if pricing_url.rstrip("/") != page["url"].rstrip("/"):
            try:
                pages.append(self._page(pricing_url))
            except FetchError:
                pass
        text = " ".join(p["text"] for p in pages)
        content = (f"Component needed: {component.capability} — {component.description}\n"
                   f"Page title: {page['title']}\nURL: {page['url']}\n"
                   f"Page text: {' '.join(p['text'][:6000] for p in pages)}")
        extracted: ExtractedTool = self.meter.model_call(self.model, "extract", EXTRACT_INSTRUCTIONS, content, ExtractedTool)
        if not extracted.is_vendor_product_page or component.capability not in extracted.capabilities:
            return None, None
        if not quote_on_page(extracted.description_quote, text):
            return None, f"Discarded {host}: its description could not be verified on the page."
        evidence = [Evidence(url=page["url"], quote=extracted.description_quote[:300])]
        plans, dropped = [], 0
        for p in extracted.plans:
            verified = (p.monthly_price is not None and quote_on_page(p.price_quote, text)
                        and price_in_quote(p.monthly_price, p.price_quote))
            if verified:
                plans.append(Plan(name=p.name[:60], monthly_price=p.monthly_price, billing=p.billing,
                                  per_seat=p.per_seat, evidence=p.price_quote[:200]))
                evidence.append(Evidence(url=pages[-1]["url"], quote=p.price_quote[:200]))
            elif p.monthly_price is not None:
                dropped += 1
        notes = ["Found via web research — not yet vetted by our team."]
        if dropped:
            notes.append(f"{dropped} price(s) were dropped because they could not be verified on the page.")
        if not plans:
            notes.append("Pricing not found on the vendor's site.")
        candidate = Candidate(tool_id=f"web:{host}", name=extracted.name[:80] or host, source="web", vetted=False,
                              capabilities=list(extracted.capabilities), site=page["url"],
                              description=extracted.description_quote[:300], plans=plans,
                              pricing_source_url=pages[-1]["url"] if plans else "",
                              evidence=evidence, notes=notes)
        self.store.save_discovered(candidate)
        return candidate, None
