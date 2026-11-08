"""Web research for components the in-house catalog cannot cover.

Search finds candidate vendor pages, a cheap model extracts structured facts, and
application code keeps only facts whose quoted evidence appears on the fetched page.
Web candidates are always unvetted.
"""
from __future__ import annotations

import os
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlparse

import requests

from .budget import BudgetExceeded, Meter
from .capabilities import CAPABILITIES
from .safe_fetch import FetchError, Page, SafeFetcher
from .schemas import ArticleTools, Candidate, Component, Evidence, ExtractedTool, Intake, Plan

# Review and media sites publish comparison articles worth reading, but are never vendors.
REVIEW_MEDIA_DOMAINS = {
    "g2.com", "capterra.com", "getapp.com", "softwareadvice.com", "trustradius.com", "medium.com",
    "forbes.com", "techradar.com", "pcmag.com", "zdnet.com", "nerdwallet.com", "businessnewsdaily.com",
    "investopedia.com", "producthunt.com", "zapier.com",
}
# Social, video and forum sites are skipped entirely.
NON_SOURCE_DOMAINS = {"reddit.com", "quora.com", "youtube.com", "wikipedia.org", "linkedin.com",
                      "facebook.com", "instagram.com", "x.com", "twitter.com", "tiktok.com", "github.com"}
SKIP_DOMAINS = REVIEW_MEDIA_DOMAINS | NON_SOURCE_DOMAINS  # never vendor leads


def in_domains(host, domains):
    return any(host == d or host.endswith("." + d) for d in domains)

EXTRACT_INSTRUCTIONS = """You extract facts about a software product from the text of its own website.
Rules:
- Only use the provided page text. Never use outside knowledge.
- is_vendor_product_page is true only if the page is the official site of a software product (not a review, list or blog).
- fits_need is true only if the product's main purpose fulfils the "Component needed" described
  below (not a side feature or an integration).
- name: the product name without taglines (e.g. "Typeform"). If the product name is generic,
  include the company (e.g. "RingCentral AI Receptionist", not "AI Receptionist").
- capabilities: only the product's primary purposes as the page presents them. Do not include a tag
  for features that are merely mentioned, integrations, or side features. If unsure, leave it out.
- description_quote: copy one sentence verbatim from the page text describing what the product does.
- plans: for each plan whose monthly price is stated, copy the exact text stating the price into price_quote.
  If no price is stated, set monthly_price to null. Use billing "annual" if the price is only for yearly billing.
- api_quote: copy verbatim text mentioning an API or developer platform, or "" if none.
- audience: who the page says the product is for, in a few words (e.g. "small local businesses",
  "enterprise contact centers", "dental practices", "developers building voice apps").
- small_business_fit: true only if a small business like the one described under "Business" could
  sign up and use it: it is marketed to small or local businesses, or offers self-serve plans for a
  single business. False if it targets enterprises only, or is built for a different specific industry
  than the business type given (e.g. a dental-practice tool for a plumbing business).
- setup_complexity: "no-code" if ready to use after sign-up, "low-code" if configured in a visual
  builder, "developer" if it needs coding or an API to set up, "unknown" if the page doesn't say.
Allowed capability tags: """ + ", ".join(CAPABILITIES)

MINE_INSTRUCTIONS = """You read a comparison or "best tools" article and list the software products it recommends.
Rules:
- Only use the article text. Never add products from outside knowledge.
- List each product at most once, using its name exactly as written in the article.
- best_for: the article's own short note on what the product is best for, or "" if none.
- url: the product's own website if it appears in the article's links or text, else null.
- Skip products that do not fulfil the "Need" described below, and skip the article publisher's
  own product unless the article presents it as one of the options."""


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
                                        "exclude_domains": ",".join(sorted(NON_SOURCE_DOMAINS)),
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
                                     json={"urls": [url], "format": "markdown", "links": True,
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
        links = [u for u in (r.get("links") or []) if isinstance(u, str) and u.startswith("https://")][:300]
        return Page(url=r.get("final_url") or url, title=(r.get("title") or "")[:160],
                    description=(r.get("description") or "")[:800], headings="", text=text, links=links)


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
ARTICLE_PATH = re.compile(
    r"/(blog|blogs|blog-posts|posts|article|articles|guide|guides|news|publications|resources|learn|insights)(/|$)"
    r"|/best-", re.I)


LISTICLE_TITLE = re.compile(r"\b(best|top \d+|\d+ (best|top)|alternatives|vs\.?|versus|compared|comparison|review(s|ed)?)\b", re.I)
GENERIC_NAME_WORDS = {"ai", "the", "app", "apps", "inc", "llc", "io", "hq", "voice", "agent", "agents", "receptionist",
                      "answering", "service", "services", "software", "platform", "phone", "call", "calls"}
BRAND_PREFIXES = ("get", "try", "use", "join", "hey", "go")


def looks_like_article(url):
    return bool(ARTICLE_PATH.search(urlparse(url).path or ""))


def looks_like_listicle(result):
    return looks_like_article(result.url) or bool(LISTICLE_TITLE.search(result.title or ""))


def brand_of(host):
    brand = host.split(".")[0]
    for prefix in BRAND_PREFIXES:  # getweave.com -> weave
        if brand.startswith(prefix) and len(brand) - len(prefix) >= 3:
            return brand[len(prefix):]
    return brand


def name_matches_host(name, host):
    """'Smith.ai' ~ smith.ai, 'Goodcall' ~ goodcall.com, 'Carly AI' ~ carly.ai (not ai-tools.com)."""
    brand = brand_of(host).replace("-", "")
    compact = re.sub(r"[^a-z0-9]", "", name.lower())
    tokens = [t for t in re.findall(r"[a-z0-9]+", name.lower()) if t not in GENERIC_NAME_WORDS and len(t) >= 3]
    return bool(brand) and len(brand) >= 3 and (brand in compact or any(t == brand or brand.startswith(t) for t in tokens))


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


def clean_tool_name(name):
    """'LobbyStack (ours)' -> 'LobbyStack'; drop parenthetical asides and trailing punctuation."""
    return re.sub(r"\s*\([^)]*\)", "", name or "").strip(" .,:;-") or (name or "").strip()


def display_name(name, host):
    """Prefix the company (from the domain) when the product name doesn't mention it."""
    name = (name or "").strip()[:80]
    brand = brand_of(host)
    if not name:
        return host
    compact = name.lower().replace(" ", "").replace("-", "")
    words = [w for w in re.findall(r"[a-z0-9]+", name.lower()) if len(w) >= 4 and w not in GENERIC_NAME_WORDS]
    if brand and brand.replace("-", "") not in compact and not any(w in brand for w in words):
        return f"{brand.capitalize()} {name}"
    return name


def base_host(url):
    host = (urlparse(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


@dataclass
class Lead:
    """A vendor site worth checking, from search results or from comparison articles."""
    url: str
    host: str
    name: str = ""
    best_for: str = ""
    mentions: int = 0


COMPLEXITY_ORDER = {"no-code": 0, "low-code": 1, "unknown": 2, "developer": 3}


def article_score(result):
    """Independent comparisons first; a vendor's own guide or blog post last."""
    title = result.title or ""
    score = 0
    if LISTICLE_TITLE.search(title):
        score += 2  # "best", "top 10", "compared", "alternatives"
    if re.search(r"\b\d+\b", title):
        score += 1  # numbered lists name several tools
    if in_domains(base_host(result.url), REVIEW_MEDIA_DOMAINS):
        score += 1  # review and media publishers
    return score


class Researcher:
    max_articles = 4

    def __init__(self, store, meter: Meter, model, search: SearchClient | None, fetcher, catalog,
                 candidates_per_component=None):
        self.store, self.meter, self.model, self.search, self.fetcher = store, meter, model, search, fetcher
        # Check up to this many vendor sites per part, in parallel, to surface real alternatives.
        self.candidates_per_component = candidates_per_component or int(os.environ.get("AGENT_RESEARCH_CANDIDATES", 8))
        self.catalog_hosts = {base_host(t.get("site", "")) for t in catalog}

    def queries(self, component: Component, intake: Intake):
        """The breakdown step writes a plain search query per part; "best ..." surfaces comparison
        articles and the bare query surfaces vendor sites."""
        query = " ".join((component.search_query or "").split()).rstrip(".").lower()
        if not query:
            need = component.description if component.capability == "other" else CAPABILITIES.get(component.capability, "")
            query = f"{need.rstrip('.').lower()} software for small business"
        return [f"best {query}", query]

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
        data = {"url": page.url, "title": page.title, "text": page.text[:20_000], "links": page.links[:300]}
        self.store.cache_put(self.meter.run_id, "page", url, data)
        return data

    def _skippable(self, host):
        return (not host or host in self.catalog_hosts
                or in_domains(host, SKIP_DOMAINS))

    def research(self, component: Component, intake: Intake) -> tuple[list[Candidate], list[str]]:
        label = component.capability.replace("_", " ") if component.capability != "other" else component.description
        if self.search is None:
            return [], [f"Web research unavailable for '{label}': no search API key is configured."]
        notes, leads, found_articles = [], {}, {}
        try:
            for query in self.queries(component, intake):
                for result in self._search(query):
                    host = base_host(result.url)
                    if not host or in_domains(host, NON_SOURCE_DOMAINS):
                        continue
                    if looks_like_listicle(result) or in_domains(host, REVIEW_MEDIA_DOMAINS):
                        found_articles.setdefault(result.url, result)
                    elif not self._skippable(host) and host not in leads:
                        leads[host] = Lead(url=result.url, host=host)
            # Pick the best articles from all queries, not just the first ones returned.
            ranked = sorted(found_articles.values(), key=article_score, reverse=True)
            articles = [r.url for r in ranked if article_score(r) >= 2][: self.max_articles]
            for lead in self._mine_articles(component, articles, notes):
                if lead.host in leads:
                    leads[lead.host].mentions += lead.mentions
                    leads[lead.host].best_for = leads[lead.host].best_for or lead.best_for
                else:
                    leads[lead.host] = lead
        except requests.RequestException:
            notes.append(f"Web search for '{label}' failed; try again later.")
        except BudgetExceeded as exc:
            notes.append(f"Research for '{label}' stopped early: {exc}.")
        # Tools recommended by several articles first; each check is independent, so run them in parallel.
        ranked = sorted(leads.values(), key=lambda l: -l.mentions)
        candidates = self._evaluate_all(component, intake, ranked[: self.candidates_per_component], notes, label)
        candidates.sort(key=lambda c: (COMPLEXITY_ORDER.get(c.setup_complexity, 2), -c.mentions))
        if not candidates:
            notes.append(f"No tool with verifiable evidence was found on the web for '{label}'.")
        return candidates, notes

    def _evaluate_all(self, component, intake, batch, notes, label):
        candidates = []
        if not batch:
            return candidates
        with ThreadPoolExecutor(max_workers=len(batch)) as pool:
            futures = [pool.submit(self._evaluate, component, intake, lead) for lead in batch]
            stopped = False
            for future in futures:
                try:
                    candidate, note = future.result()
                except BudgetExceeded as exc:
                    if not stopped:
                        notes.append(f"Research for '{label}' stopped early: {exc}.")
                    stopped = True
                    continue
                if note:
                    notes.append(note)
                if candidate:
                    candidates.append(candidate)
        return candidates

    def _mine_articles(self, component, article_urls, notes) -> list[Lead]:
        """Read comparison articles and turn the products they recommend into leads."""
        found: dict[str, Lead] = {}
        for url in article_urls:
            try:
                page = self._page(url)
            except FetchError:
                continue
            article_host = base_host(page["url"])
            link_hosts = {}
            for link in page.get("links", []):
                h = base_host(link)
                if h and h != article_host and not self._skippable(h):
                    link_hosts.setdefault(h, link)
            content = (f"Need: {component.description}\nArticle title: {page['title']}\n"
                       f"Websites linked from the article: {', '.join(sorted(link_hosts)[:150])}\n"
                       f"Article text: {page['text'][:12000]}")
            mined: ArticleTools = self.meter.model_call(self.model, "mine", MINE_INSTRUCTIONS, content, ArticleTools)
            for tool in mined.tools[:15]:
                if not quote_on_page(tool.name, page["text"]):
                    continue  # the article must actually name the product
                tool.name = clean_tool_name(tool.name)
                if name_matches_host(tool.name, article_host):
                    continue  # a vendor listing itself in its own article is not a recommendation
                host = self._resolve_host(tool, link_hosts)
                if not host or self._skippable(host) or host == article_host:
                    continue
                url = link_hosts.get(host) or (tool.url if tool.url and base_host(tool.url) == host else f"https://{host}/")
                lead = found.setdefault(host, Lead(url=url, host=host, name=tool.name))
                lead.mentions += 1
                lead.best_for = lead.best_for or tool.best_for[:120]
        if found:
            notes.append(f"Found {len(found)} tool(s) recommended in {len(article_urls)} comparison article(s).")
        return list(found.values())

    def _resolve_host(self, tool, link_hosts):
        """The product's own domain: a linked site whose name matches, else a quick search."""
        if tool.url:
            h = base_host(tool.url)
            if h in link_hosts or name_matches_host(tool.name, h):
                return h
        for h in link_hosts:
            if name_matches_host(tool.name, h):
                return h
        try:
            for result in self._search(f"{tool.name} official site"):
                h = base_host(result.url)
                if not looks_like_listicle(result) and not self._skippable(h) and name_matches_host(tool.name, h):
                    return h
        except (BudgetExceeded, requests.RequestException):
            pass
        return None

    def _evaluate(self, component, intake, lead):
        host = lead.host
        try:
            page = self._page(lead.url)
        except FetchError as exc:
            return None, f"Skipped {lead.name or host}: {exc}"
        pages = [page]
        pricing_url = f"https://{urlparse(page['url']).hostname}/pricing"
        if pricing_url.rstrip("/") != page["url"].rstrip("/"):
            try:
                pages.append(self._page(pricing_url))
            except FetchError:
                pass
        text = " ".join(p["text"] for p in pages)
        content = (f"Component needed: {component.capability} — {component.description}\n"
                   f"Business: {intake.business_type or 'small business'}"
                   f"{' (' + intake.business_name + ')' if intake.business_name else ''} — {intake.problem_text[:300]}\n"
                   f"Page title: {page['title']}\nURL: {page['url']}\n"
                   f"Page text: {' '.join(p['text'][:6000] for p in pages)}")
        extracted: ExtractedTool = self.meter.model_call(self.model, "extract", EXTRACT_INSTRUCTIONS, content, ExtractedTool)
        if not extracted.is_vendor_product_page or not extracted.fits_need:
            return None, None
        # Tagged needs must also carry the tag; 'other' needs rely on fits_need alone.
        if component.capability != "other" and component.capability not in extracted.capabilities:
            return None, None
        name = display_name(extracted.name or lead.name, host)
        if not extracted.small_business_fit:
            return None, f"Skipped {name}: aimed at {extracted.audience or 'a different audience'}, not small businesses."
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
        if lead.mentions:
            notes.append(f"Recommended in {lead.mentions} comparison article(s).")
        if dropped:
            notes.append(f"{dropped} price(s) were dropped because they could not be verified on the page.")
        if not plans:
            notes.append("Pricing not found on the vendor's site.")
        candidate = Candidate(tool_id=f"web:{host}", name=name, source="web", vetted=False,
                              capabilities=list(extracted.capabilities), site=page["url"],
                              description=extracted.description_quote[:300], plans=plans,
                              pricing_source_url=pages[-1]["url"] if plans else "",
                              evidence=evidence, notes=notes, audience=extracted.audience[:80],
                              setup_complexity=extracted.setup_complexity, best_for=lead.best_for,
                              mentions=lead.mentions)
        self.store.save_discovered(candidate, component.description)
        return candidate, None
