"""Can two chosen tools actually exchange data? Checked on the web, with quoted evidence.

Native: one vendor's page mentions the other tool next to integration wording, and the quote is on the
page. Zapier: both tools have a Zapier app page. Results are cached per pair of tools for 30 days.
"""
from __future__ import annotations

import re

import requests

from .research import GENERIC_NAME_WORDS, base_host, quote_on_page
from .safe_fetch import FetchError

LINK_WORDS = re.compile(r"\b(integrat\w*|connect\w*|sync\w*|works with|add-on|app marketplace)\b", re.I)


def core_name(name: str) -> str:
    """The distinctive word to look for on a page: 'Square Marketing' -> 'square', 'HubSpot' -> 'hubspot'."""
    words = [w for w in re.findall(r"[a-z0-9]+", (name or "").lower()) if len(w) >= 3 and w not in GENERIC_NAME_WORDS]
    return words[0] if words else (name or "").lower()


def pair_key(a: dict, b: dict) -> str:
    return "|".join(sorted([core_name(a["tool"]) + "@" + base_host(a.get("site", "")),
                            core_name(b["tool"]) + "@" + base_host(b.get("site", ""))]))


def _page_text(fetcher, url):
    try:
        return fetcher.fetch(url).text
    except FetchError:
        return ""


DETAIL_WORDS = re.compile(r"\b(trigger|action|when|then|new|create|update|find|sync|automatically)\b", re.I)


def _excerpt(text, around=None, limit=3000):
    """Page text worth giving the guide writer: around a quote, or the parts naming triggers and actions."""
    if around and around in text:
        i = text.index(around)
        return text[max(0, i - 1200): i + 1800][:limit]
    parts = [p for p in _sentences(text) if DETAIL_WORDS.search(p)]
    return " ".join(parts)[:limit] or text[:limit]


def _sentences(text):
    return re.split(r"(?<=[.!?])\s+", text)


def check_pair(store, search, fetcher, a: dict, b: dict) -> dict:
    key = pair_key(a, b)
    cached = store.get_connection(key)
    if cached is not None:
        return cached
    if search is None:
        return {"checked": False, "native": None, "zapier": {}}
    result = {"checked": True, "native": None, "zapier": {}}
    names = {s["id"]: core_name(s["tool"]) for s in (a, b)}
    hosts = {base_host(s.get("site", "")) for s in (a, b)} - {""}
    try:
        # Native integration: a vendor page that mentions the other tool next to integration wording.
        for r in search.search(f"{a['tool']} {b['tool']} integration")[:6]:
            host = base_host(r.url)
            if host not in hosts:
                continue
            text = _page_text(fetcher, r.url)
            other = names[b["id"]] if host == base_host(a.get("site", "")) else names[a["id"]]
            for sentence in _sentences(text):
                low = sentence.lower()
                if other in low and LINK_WORDS.search(sentence) and quote_on_page(sentence, text):
                    result["native"] = {"url": r.url, "quote": sentence.strip()[:240]}
                    result["details"] = {"url": r.url, "text": _excerpt(text, sentence.strip())}
                    break
            if result["native"]:
                break
        # Zapier: each tool has an app page on zapier.com.
        for step in (a, b):
            for r in search.search(f"zapier.com {step['tool']} integrations")[:6]:
                if base_host(r.url).endswith("zapier.com") and "/apps/" in r.url:
                    if names[step["id"]] in _page_text(fetcher, r.url).lower():
                        result["zapier"][step["id"]] = r.url
                    break
        # Zapier's page for this exact pair lists the real triggers and actions the guide may use.
        slugs = [re.search(r"/apps/([^/?#]+)", result["zapier"].get(step["id"], "")) for step in (a, b)]
        if not result.get("details") and all(slugs):
            url = f"https://zapier.com/apps/{slugs[0].group(1)}/integrations/{slugs[1].group(1)}"
            text = _page_text(fetcher, url)
            if names[a["id"]] in text.lower() and names[b["id"]] in text.lower():
                result["details"] = {"url": url, "text": _excerpt(text)}
    except requests.RequestException:
        return {"checked": False, "native": None, "zapier": {}}
    store.save_connection(key, result)
    return result


def route_for(edge: dict, src: dict, dst: dict, check: dict | None) -> dict:
    """The best way to connect two steps: native first, then Zapier, otherwise not verified."""
    if edge["kind"] == "built_in":
        return {"route": "built_in", "label": f"Built into {src['tool']}", "sources": []}
    if edge["kind"] == "manual":
        return {"route": "manual", "label": "Handled outside the plan", "sources": []}
    check = check or {}
    zap = check.get("zapier") or {}
    zapier_ok = src["id"] in zap and dst["id"] in zap
    if check.get("native"):
        note = "Zapier also works." if zapier_ok else ""
        return {"route": "native", "label": "Native integration ✓", "note": note,
                "sources": [check["native"]["url"]], "quote": check["native"]["quote"]}
    if zapier_ok:
        return {"route": "zapier", "label": "Via Zapier ✓ (both apps listed)", "sources": [zap[src["id"]], zap[dst["id"]]]}
    if not check.get("checked"):
        return {"route": "unchecked", "label": "Connection not checked yet", "sources": []}
    return {"route": "unverified", "label": "Not verified: you may need to export and import manually", "sources": []}
