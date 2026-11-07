"""Run one web search through the agent's search client and print the results.

Usage:
  python scripts/try_search.py                              # default query
  python scripts/try_search.py "online booking software for salons pricing"
  python scripts/try_search.py --count 3 "..."

Uses TinyFish if TINYFISH_API_KEY is set (in .env or your shell), otherwise Brave.
Makes exactly one search call; no pages are fetched and no model is used.
Each result is marked with whether the agent's research step would consider it.
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from agent.catalog import load_catalog  # noqa: E402
from agent.research import SKIP_DOMAINS, base_host, default_search, looks_like_article  # noqa: E402

CATALOG_HOSTS = {base_host(t.get("site", "")) for t in load_catalog()}

DEFAULT_QUERY = "software to collect website contact form submissions for small business pricing"


def verdict(url):
    host = base_host(url)
    if host in CATALOG_HOSTS:
        return "skip: already in our catalog"
    if any(host == d or host.endswith("." + d) for d in SKIP_DOMAINS):
        return "skip: review/media site"
    if looks_like_article(url):
        return "skip: article"
    return "candidate"


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("query", nargs="*", help="search query (defaults to a contact-form example)")
    parser.add_argument("--count", type=int, default=8, help="max results to show (default 8)")
    args = parser.parse_args()

    client = default_search()
    if client is None:
        sys.exit("No search key found. Add TINYFISH_API_KEY (or BRAVE_API_KEY) to .env.")
    query = " ".join(args.query) or DEFAULT_QUERY
    print(f"Provider: {client.provider}\nQuery:    {query}\n")
    try:
        results = client.search(query, count=args.count)
    except Exception as exc:  # show the failure plainly instead of a traceback
        sys.exit(f"Search failed: {type(exc).__name__}: {exc}")
    if not results:
        print("No results.")
        return
    for i, r in enumerate(results, start=1):
        print(f"{i}. {r.title[:80]}\n   {r.url}\n   [{verdict(r.url)}] {r.snippet[:140]}\n")
    candidates = sum(verdict(r.url) == "candidate" for r in results)
    print(f"{len(results)} results, {candidates} would be checked by the research step.")


if __name__ == "__main__":
    main()
