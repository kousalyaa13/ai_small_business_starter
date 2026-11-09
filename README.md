# BloomBot AI — software advice for small businesses

A hackathon proof of concept that helps small-business owners evaluate whether they need AI, compare a few suitable options, preview a workflow, and plan a limited pilot.

| Route | What it is |
|---|---|
| `/` | Public landing page (see `LANDING_PAGE.md`) |
| `/solutions` | What the product does and how sources are checked |
| `/dashboard` | One short questionnaire, one decision dashboard (below) |
| `/consult` | Consultation agent: describe a problem, get researched tools per need, pick them, get a setup guide |

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

Browse to http://127.0.0.1:5050 (set `PORT` to change it; macOS reserves port 5000 for AirPlay Receiver, which returns "403 Access denied"). The dashboard (`/dashboard`) uses `templates/dashboard.html`. This one file contains the dashboard HTML, CSS, and vanilla JavaScript to make hackathon editing straightforward.

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
3. **Rates fit, then searches the web for gaps.** Every known tool is rated for each need and business type as "Built for this", "Works, with limits" or "Not designed for this". A need is researched only if fewer than 2 known tools are built for it. The breakdown step writes a plain search query per part (e.g. "AI receptionist for plumbers"). Research reads up to 4 independent comparison articles to collect the tools they recommend (a vendor's mention of itself doesn't count), checks each tool's own site for price, audience and setup difficulty, and drops tools aimed at enterprises or other industries. It uses TinyFish (or Brave) to search, then reads vendor pages through TinyFish's browser-based fetch or an SSRF-guarded direct fetcher. A model extracts facts, and code keeps only those whose quoted evidence appears on the page. Web finds are always labelled *not vetted* and are saved to the `discovered_tools` table. A later request for the same need reuses them instead of searching again, and rediscovering a tool refreshes its prices. Needs outside the fixed capability list (such as answering phone calls) are matched by their wording. Entries older than 30 days are researched again.
4. **Shows the top 5 tools per need.** Monthly costs are calculated in code from the plans and your usage. A tool covering several parts is counted once, and unknown prices are never guessed. The owner picks one tool per need, or marks it "I already have this" or "Skip". As picks change, the page prices the selection and checks whether the picked tools can exchange data (native integration, Zapier, or by hand).
5. **Builds a setup plan.** Once every need has a choice, **Build my setup guide** saves the selection (the LangGraph run is checkpointed in SQLite, so it survives restarts; approvals are idempotent). The setup plan shows how the tools connect (when each connection runs, what data moves and how) and one ordered step-by-step guide across all tools, with checkboxes saved per project. Plans with unconfirmed prices can still be saved; the guide adds a step to confirm the price with the vendor. The plan and guide can be downloaded as a PDF.

Nothing is purchased, connected or configured for the owner; the guide tells them how to do it.

### Research speed

Measured on one need ("restaurant reservation system"), research takes about 45–60 s, down from about 100 s:

- Needs are researched in parallel (up to 3 at a time, `AGENT_RESEARCH_WORKERS`).
- The 4 comparison articles are read and mined in parallel; results are merged in article order, so the outcome doesn't depend on which finishes first.
- Homepages are looked up by search only for the top 5 tools that will actually be checked, not for every tool an article names. The lookup prefers the exact brand (Tock → exploretock.com, not tockos.org).
- Vendor `/pricing` pages are optional, so they get an 8 s timeout instead of 30 s.
- Vendor pages are checked in parallel.

Cheaper mining (minimal reasoning, or 8,000 instead of 12,000 characters of article text) was A/B tested on three needs and rejected: it dropped up to half of the tools the articles recommend. What remains is mostly model time (about 10–15 s per article and per vendor page, in parallel).

## Product-first consultation interface (November 2026)

The `/consult` results page is now designed for nontechnical business owners:

- A compact **recommendations ready** summary replaces the seven progress chips after the run finishes. Identified tasks are available in a single optional disclosure.
- Each recommendation is a **product-first card** with a prominent vendor name, recognizable site icon, plan, total price, billing commitment, task coverage, and **Visit website** link. Site icons are loaded from the vendor's own HTTPS site (`/favicon.ico`); a letter fallback is shown if an icon is unavailable.
- Tasks fulfilled by the **same product appear once** under that product. Included capabilities do not add duplicate charges. Bundled options show distinct products separately.
- Each tool shows where it came from: **Reviewed listing** (from the curated catalog) or **Web-discovered**. **View evidence** shows the recorded source links. These checks confirm sources are recorded, not that a vendor is safe; the security assessment is labelled as a demo preview.
- **Compare tools** switches to a consistent side-by-side table; the old per-task "other tool fits this part" links are removed from the customer-facing view.
- **View details** reveals three concise tabs: Overview, Pricing details (with sources and full assumptions), and Setup & limits. Setup budget remains optional and collapsed.
- **Adjust budget or team size** opens the existing recovery calculator only when requested, unless no viable match exists. Nothing is purchased; **Choose this tool** saves the plan selection.

The pricing and suitability logic remains server-side and unchanged by this visual redesign. The added `Assignment` metadata exposes the vendor website, pricing source, checked date and description to the UI. Existing saved plans lacking these optional fields still render with a letter fallback and available source links.

### UI verification

```bash
python -m unittest discover -s tests -v
node --check static/consult-results.js
node --check static/consult-recovery.js
node tests/test_consult_results.js
```

Manually test `/consult` at desktop and mobile widths with (1) one tool covering multiple tasks, (2) a two-tool combination, (3) a request with no match, (4) budget/editor adjustments, and (5) an approved plan. Product icons require an internet connection and may be blocked by privacy settings; vendor names and links remain available.

### Budget and seat-mix recovery (November 2026)

The consultation now recognizes fiscal-year **budget development, budget tracking,
financial reporting and budget approvals** rather than classifying those requests as
`other`. A request such as "Need to manage my budget development and tracking
every fiscal year. Budget of 75 per month. 5 people need access." extracts the
explicit **$75/month** budget and **five total users**. If the owner hasn't stated
how many need editing rights, the agent asks one focused clarification.

Once options are available, the consultation includes **What could I change?**:
- A slider for paid editors versus read-only viewers (never assumes viewers are free
  unless the vendor's catalog plan explicitly confirms that).
- An editable monthly budget and a verified-price comparison showing plans under
  budget and the nearest over-budget alternatives with exact monthly/annual
  differences.
- Official vendor pricing links and dates, with annual commitments clearly labeled.
- An optional clarified problem description and **Update & find better options**,
  which saves a new requirement version and reruns the consultation within the same
  project. Previous plans remain in history, and stale approvals are rejected.
- A useful clarification/manual-first message when no vetted product can be
  matched; unknown prices are never generated by AI, and unresolved plans cannot
  be approved.

The reviewed catalog now includes **Smartsheet Pro/Business** and **Airtable Team**
plans for configurable collaborative fiscal-budget workflows. Pricing was checked
on 2026-11-08 at
[Smartsheet](https://www.smartsheet.com/pricing) and
[Airtable](https://airtable.com/pricing), with read-only collaborator details
confirmed on the official pages. These products are configurable planning
platforms, **not guaranteed financial management systems**; owners must verify
their actual reporting, approvals, import, security and compliance requirements.
All figures exclude taxes, setup and optional add-ons.

API: `POST /api/projects/<id>/revise` accepts a subset of
`{"monthly_budget":75,"editors":3,"seats":5,"problem_text":"..."}` and returns
HTTP 202 with the new run and requirement version. The existing status endpoint
includes a `recovery` object with verified catalog plan comparisons.

### Configuration (all optional)

Put keys in a `.env` file in the project folder (copy `.env.example`). It is git-ignored and loaded automatically by `app.py` and `scripts/try_decompose.py`; variables exported in your shell take precedence.

| Variable | Purpose |
|---|---|
| `OPENAI_API_KEY` | Real decomposition and extraction. Without it, a deterministic keyword stub is used. |
| `OPENROUTER_API_KEY` | Used when `OPENAI_API_KEY` is not set. Defaults: `openai/gpt-5-mini` for decomposition and the setup guide, `openai/gpt-5-nano` for extraction, article mining and fit ratings. `OPENROUTER_MODEL` sets one model for all; `AGENT_MODEL_<TASK>` sets one task. |
| `AGENT_REASONING_<TASK>` | Reasoning effort per task (`none`, `minimal`, `low`, `medium`, `high`). Tasks: `decompose`, `extract` (vendor pages), `mine` (comparison articles), `fit` (fit ratings), `setup` (setup guide). Defaults: `low` for extract, mine and fit; `medium` for setup. `minimal` was tested and is faster, but missed tools and mislabelled capabilities. |
| `AGENT_RESEARCH_CANDIDATES`, `AGENT_MIN_GOOD_TOOLS` | Vendor sites checked per researched need (default 5), and how many known tools rated "built for this" a need needs before web research is skipped (default 2). The page shows the top 5 tools per need. |
| `AGENT_RESEARCH_WORKERS` | Needs researched at the same time (default 3, max 4). |
| `AGENT_MINE_TEXT_CHARS` | Characters of each comparison article given to the model (default 12000; lower values lost tools in testing). |
| *(fit ratings)* | Every tool is rated for each need and business type as "Built for this", "Works, with limits" or "Not designed for this", with a reason. Tools found online are rated while their pages are read; catalog and saved tools in one batched call per run, cached by tool, need and business type. A need counts as covered only if some tool is built for it, otherwise it is researched. "Reviewed listing" means the price and details were checked, not that the tool fits. |
| `AGENT_DISCOVERED_MAX_AGE_DAYS` | How long a web-discovered tool is reused before it is researched again (default 30). |
| `AGENT_PROVIDER` | Force `openai`, `openrouter` or `stub`, regardless of which keys are set. |
| `OPENAI_MODEL`, `AGENT_MODEL_<TASK>` | Model per task (defaults to `OPENAI_MODEL`). |
| `TINYFISH_API_KEY` | Web research via TinyFish: its Search API finds vendors, and its Fetch API reads pages in a real browser. Used before Brave when both are set. |
| `BRAVE_API_KEY` | Alternative search provider (pages are then fetched directly). Without any search key, gaps are reported. |
| `AGENT_MAX_SEARCHES`, `AGENT_MAX_FETCHES`, `AGENT_MAX_MODEL_CALLS`, `AGENT_MAX_RUN_TOKENS`, `AGENT_MAX_RUN_USD` | Hard per-run limits. |
| `AGENT_PRICE_IN_PER_MTOK`, `AGENT_PRICE_CACHED_IN_PER_MTOK`, `AGENT_PRICE_OUT_PER_MTOK`, `AGENT_PRICE_PER_SEARCH` | Unit prices for spend estimates. If unset, spend is recorded as unknown and the token limit applies. |

API:

| Endpoint | Purpose |
|---|---|
| `POST /api/projects` | Start a consultation from the problem description |
| `GET /api/projects/<id>` | Run status, plan, picks, approval and setup plan (the page polls this) |
| `POST /api/projects/<id>/answers` | Answer clarifying questions |
| `POST /api/projects/<id>/research` | Find cheaper or better options (see below) |
| `POST /api/projects/<id>/revise` | Change budget, seats or problem text and rerun |
| `POST /api/projects/<id>/selection` | Price the owner's current picks (nothing is saved) |
| `POST /api/projects/<id>/connections` | Check how the picked tools connect |
| `POST /api/projects/<id>/approve` | Save the picks and start building the setup plan |
| `POST /api/projects/<id>/setup/progress` | Tick a setup-guide step done or not done |
| `POST /api/projects/<id>/setup/retry` | Rebuild a setup plan that failed |
| `GET /api/projects/<id>/plan.pdf` | Download the plan and setup guide as a PDF |

**Find cheaper or better options.** Parts covered by the catalog or by saved discoveries are not searched again automatically, so the customer can ask for more: each tool on a result card has a "Find cheaper or better options" button, and the toolbar has one for every part. `POST /api/projects/<id>/research` with `{"component_ids": [...], "goal": "cheaper" | "better" | "both"}` (no ids means every part) starts a follow-up run that reuses the current parts, searches for alternatives to the tools already shown and cheaper ones for the business type, skips tools already listed, and saves a new plan version. It is refused while another search runs or after a plan is approved, and approval is refused while the search runs.

### Not yet verified
- Decomposition, extraction and setup-guide quality with a real model across many businesses. The tests use the stub; real-model checks so far are manual spot checks and the mining A/B test above.
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

## Deploy

Any host that keeps a normal server process running works (e.g. Render). Serverless hosts such as Vercel don't: consultations run in background threads after the request returns.

- Start command: `gunicorn app:app --workers 1 --threads 8 --timeout 120`. Use one worker, because running consultations live in that process; threads let the page keep polling while research runs.
- Set the keys from `.env` in the host's environment settings; `.env` is git-ignored.
- SQLite files are wiped on free hosts when they restart, so saved projects and discovered tools start fresh. Set `DB_PATH` to a persistent disk for anything beyond a demo.
- A public link spends your OpenRouter credit on every consultation, so set a credit limit on the key.

## Technical stack

| Layer | Technology | Used for |
|---|---|---|
| Language | Python 3.10+ (CI runs 3.11) | Backend and agent |
| Web server | Flask 3, Jinja templates, gunicorn | Pages and JSON API (`app.py`, `agent/api.py`) |
| Frontend | HTML, CSS and vanilla JavaScript (no framework or build step) | Landing page, dashboard, consultation and setup-plan UI (`templates/`, `static/`) |
| Agent workflow | LangGraph, with a SQLite checkpointer (`langgraph-checkpoint-sqlite`) | Breakdown → catalog match → research → options → approval, with pauses for questions and approval that survive restarts |
| Background work | Python thread pools | Runs consultations after the request returns; researches needs, reads articles and checks vendor pages in parallel |
| Language models | OpenAI Python SDK, through OpenRouter (or OpenAI directly) | Structured outputs with strict JSON schemas. `openai/gpt-5-mini` for the breakdown and setup guide; `openai/gpt-5-nano` for reading articles and vendor pages and rating fit |
| Data validation | Pydantic v2 | Request bodies, model outputs and stored records (`agent/schemas.py`) |
| Web research | TinyFish Search API and Fetch API (Brave Search as an alternative) | Finding comparison articles and vendors; reading pages in a real browser as markdown |
| Direct page fetching | requests, BeautifulSoup | Fallback fetcher with SSRF protection (`agent/safe_fetch.py`) |
| Storage | SQLite | Projects, runs, plans, approvals, discovered tools, fit and connection caches, setup progress (`agent/store.py`); curated catalog in `data/tools.json` |
| Documents and data | reportlab, pandas | Plan PDF download; CSV analysis on the dashboard |
| Configuration | python-dotenv | Loads keys from the git-ignored `.env` |
| Spend control | Per-run meter (`agent/budget.py`) | Hard limits on searches, page fetches, model calls and tokens per run |
| Testing and CI | unittest (stub model, fake search and fetch), Node.js script tests, GitHub Actions | Runs on every push to `main` (`.github/workflows/tests.yml`) |
| External assets | Google Fonts (DM Sans, Manrope), Unsplash photos, vendors' own favicons | Landing page and tool cards; the site still works if they are blocked |

## Project structure

```
app.py                     Flask API and data handling
agent/                     Consultation agent: LangGraph flow (graph.py), research (research.py),
                           fit ratings (fit.py), pricing, connections and setup plan (connections.py,
                           workflow.py, setup.py), per-run limits (budget.py), storage (store.py)
templates/landing.html     Public landing page; solutions.html explains capabilities
templates/consult.html     Problem → tools per need → setup plan page
static/consult-*.js/.css   Consultation results, budget recovery and setup-plan UI
advisor.py                 Readiness and workflow logic
templates/dashboard.html  Single-dashboard UX and browser interactions
data/tools.json            Initial tool catalog (research starting point)
static/sample_customers.csv
requirements.txt
tests/                     Python tests
```

Source code is a hackathon prototype. Validate products, integrations, pricing, safety, accessibility and business results before any real deployment.
