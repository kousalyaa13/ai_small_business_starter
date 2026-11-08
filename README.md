# BloomBot AI — one-page small business decision dashboard

A hackathon proof of concept that helps small-business owners evaluate whether they need AI, compare a few suitable options, preview a workflow, and plan a limited pilot.

## What owners actually see

The previous multi-page navigation has been removed. Complete **one short business questionnaire** and get **one dashboard** with:

1. **What should I do?** A suggested tool, brief explanation, advice on the software you already own, and an inline readiness warning.
2. **Compare and budget.** Three candidate tools with source links, access to independent reviews, and an interactive calculator for monthly labor value, costs, and first-year estimates.
3. **Your next steps.** A four-step shipment-style tracker. Mark steps complete in the browser.
4. **See how it works.** Toggle between the current and proposed process; click a step to understand it.
5. **Try an example.** Preview a sample customer email (optional OpenAI API) or relevant illustrative outputs for other use cases.
6. **Save my plan.** One print-friendly Decision Passport button in the header instead of a separate screen.

Optional research features (CSV sample analysis and official-vendor-page discovery) are tucked into **More tools for exploring** at the bottom of the dashboard rather than competing with core decisions.

Each area has a distinct visual treatment: blue for the suggested action, amber for the checklist, teal for cost/value and readiness, purple for workflow, and orange for the sample test drive. Every color is paired with text labels.

## Launch locally

Requires Python 3.10+.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Browse to http://127.0.0.1:5050 (set `PORT` to change it; macOS reserves port 5000 for AirPlay Receiver, which returns "403 Access denied"). The default route uses `templates/dashboard.html`. This one file contains the dashboard HTML, CSS, and vanilla JavaScript to make hackathon editing straightforward.

## Test

```bash
python -m unittest discover -s tests -v
```

The profile calls `POST /api/recommend`, which returns rules-based tool matches, a readiness checklist, an existing-software audit, a suggested before-and-after workflow, and steps. The page also calls `/api/preview`, `/api/analyze`, and `/api/discover` when needed.

### AI (optional)

Set `OPENAI_API_KEY` on the server for the customer-email preview and vendor page summaries; **never** expose API keys in browser code or a GitHub commit. Without a key, template previews still work. The other problem-specific test drives are clearly labeled *illustrative templates*, not vendor integrations.

## Consultation agent (`/consult`)

Describe a business problem in plain English. The agent:

1. **Breaks it into components.** Each component gets one capability tag from a fixed list (`agent/capabilities.py`). Examples: `form_capture`, `crm_contacts`, `workflow_automation`.
2. **Checks the in-house catalog first.** `data/tools.json` lists vetted tools with capabilities and plan prices, each recorded with its source URL and the date checked.
3. **Searches the web only for gaps.** It uses TinyFish (or Brave) to search, then reads vendor pages through TinyFish's browser-based fetch or an SSRF-guarded direct fetcher. A model extracts facts, and code keeps only those whose quoted evidence appears on the page. Web finds are always labelled *not vetted* and go to the `discovered` review queue.
4. **Builds two or three options.** Monthly costs are calculated in code from the plans and your usage. A tool covering several parts is counted once, and unknown prices are never guessed.
5. **Pauses for your approval.** The LangGraph run is checkpointed in SQLite, so it survives restarts. Approvals are idempotent: repeating a request returns the same record.

Nothing is purchased, connected or deployed in this milestone.

### Configuration (all optional)

Put keys in a `.env` file in the project folder (copy `.env.example`). It is git-ignored and loaded automatically by `app.py` and `scripts/try_decompose.py`; variables exported in your shell take precedence.

| Variable | Purpose |
|---|---|
| `OPENAI_API_KEY` | Real decomposition and extraction. Without it, a deterministic keyword stub is used. |
| `OPENROUTER_API_KEY` | Used when `OPENAI_API_KEY` is not set. Defaults: `openai/gpt-5-mini` for decomposition, `openai/gpt-5-nano` for extraction. `OPENROUTER_MODEL` sets one model for both. |
| `AGENT_PROVIDER` | Force `openai`, `openrouter` or `stub`, regardless of which keys are set. |
| `OPENAI_MODEL`, `AGENT_MODEL_DECOMPOSE`, `AGENT_MODEL_EXTRACT` | Model per task (the last two default to `OPENAI_MODEL`). |
| `TINYFISH_API_KEY` | Web research via TinyFish: its Search API finds vendors, and its Fetch API reads pages in a real browser. Used before Brave when both are set. |
| `BRAVE_API_KEY` | Alternative search provider (pages are then fetched directly). Without any search key, gaps are reported. |
| `AGENT_MAX_SEARCHES`, `AGENT_MAX_FETCHES`, `AGENT_MAX_MODEL_CALLS`, `AGENT_MAX_RUN_TOKENS`, `AGENT_MAX_RUN_USD` | Hard per-run limits. |
| `AGENT_PRICE_IN_PER_MTOK`, `AGENT_PRICE_CACHED_IN_PER_MTOK`, `AGENT_PRICE_OUT_PER_MTOK`, `AGENT_PRICE_PER_SEARCH` | Unit prices for spend estimates. If unset, spend is recorded as unknown and the token limit applies. |

API: `POST /api/projects`, `GET /api/projects/<id>`, `POST /api/projects/<id>/answers`, `POST /api/projects/<id>/approve`.

### Not yet verified
- Decomposition and extraction quality with a real model. The tests use the stub.
- Brave result quality, and how often vendor pricing pages are readable without a browser.
- Catalog prices were read on 2026-11-06. They include caveats (see `pricing_notes`), and Canva and Wix paid plans are unknown.

## What is and is not verified

- **AI matching scores** are illustrative calculations, not measured accuracy or vendor performance.
- **Cost values** are editable user assumptions; no real-time pricing claims or guaranteed cash savings.
- **Readiness** is self-reported, not a formal audit. Customer consent and data quality still require checking.
- **Reviews** open independent review searches (G2). The dashboard never invents quotes, stars, or reviewers. Review submissions (API only) remain pending moderation and are not public.
- **Architecture, tracker and previews** are demonstrations, not actual deployed integrations or sent messages.
- **SQLite** is used for the catalog and discovery drafts, and needs persistent storage before production deployment.
- **Privacy**: use synthetic CSVs in the public demo. The uploaded CSV is processed in-memory for the analysis endpoint.
- **Tool discovery** extracts candidates from approved vendor pages, but candidates are not published as verified recommendations.

## Demo path

1. Start with a coffee shop that uses Square and has a $75 software budget.
2. Show the recommended starting point and why existing software should be checked first.
3. Review the readiness warning, click an alternative tool, and open the real-review source.
4. Change ROI assumptions; explain uncertainty and first-year value.
5. Toggle *Today / With a tool*, click a workflow step, and run the sample test drive.
6. Check off one implementation step and click **Save my plan** to print the Decision Passport.
7. Optionally use the synthetic customer CSV or discover an official vendor page in the expanded research section.

## Project structure

```
app.py                     Flask API and data handling
agent/                     Consultation agent (LangGraph flow, catalog matching, research, pricing, approvals)
templates/consult.html     Problem → options → approval page
advisor.py                 Readiness and workflow logic
templates/dashboard.html  Single-dashboard UX and browser interactions
data/tools.json            Initial tool catalog (research starting point)
static/sample_customers.csv
requirements.txt
tests/                     Python tests
```

Source code is a hackathon prototype. Validate products, integrations, pricing, safety, accessibility and business results before any real deployment.
