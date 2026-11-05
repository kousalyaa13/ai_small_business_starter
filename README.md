# Northstar AI — Small Business Solution Lab (starter)

A runnable **student hackathon starter application**, intended for learning and extension. It has a responsive business onboarding flow, a rule-based tool matching engine, a synthetic-data CSV health check, a preview generator (template fallback or optional OpenAI API), ROI estimator, guided adoption steps, and a constrained official-page discovery feature.

**Important honesty notes:** The tool database is a research starting point, **not a verified market catalog**. All match ratings are **illustrative calculations**, not measured vendor performance. Pricing is intentionally **not fabricated**; the interface points users to the vendor for current information. The campaign preview is produced by **this app**, not by the recommended vendor. Discovery candidate records are marked **Needs review** and are **not auto-published**.

## Install and run

Requires Python 3.10+.

```bash
cd ai_small_business_starter
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Open http://127.0.0.1:5000 in your browser. You can complete the entire initial user journey **without an AI API key**; the sample marketing email will use a clearly marked template fallback.

### Add AI generation (optional but recommended for the hackathon)

Create a local `.env` file or set system environment variables. Flask won't automatically read `.env` in this starter; you can export them in your terminal:

```bash
export OPENAI_API_KEY="YOUR_OWN_KEY"
export OPENAI_MODEL="gpt-5.5"  # replace with a model you have access to
python app.py
```

On Windows PowerShell:

```powershell
$env:OPENAI_API_KEY="YOUR_OWN_KEY"
$env:OPENAI_MODEL="gpt-5.5"
python app.py
```

With a configured API key, the email preview and official-site discovery summary use OpenAI's Responses API. The core match scorer remains deterministic. OpenAI API use may incur charges. **Never put API keys into frontend JavaScript, GitHub, or your recorded video.**

## Your best 3-day build sequence

**Thursday:** Run this project; customize branding; verify the research catalog for 8–12 tools. Use vendor feature and pricing pages and note which source proves each claim. Document 2 user scenarios.

**Friday:** Improve recommendation logic and test edge cases. Add your own AI-based structured intent extraction (use Structured Outputs) so users can type their needs freely. Make the CSV diagnosis actionable; use only synthetic data for the public demo.

**Saturday:** Integrate official-page discovery, verify the sample tool records, polish accessibility, and add a comparison table showing checked price, source URL, verified-date, and integration evidence. Create a 1-minute explanation of the data model and scoring algorithm.

**Sunday before submission:** Freeze features, deploy, test from a private/incognito browser, record the demo with a backup screen recording, and submit the working link/video before the November 8, 2026 11:59pm deadline.

## What to show judges

1. Select **Coffee shop**, **customers don't come back**, **Square**, **$75/month**.
2. Get ranked recommendations with explicit score explanations. Click a vendor evidence link.
3. Open **Business health check** and upload `static/sample_customers.csv`. Explain that the dataset is synthetic and the repeat-purchase metric is *not* a churn prediction.
4. Open **Solution Lab**, generate a sample email, change the ROI assumptions, show the workflow diagram.
5. Open **Adoption roadmap** and check off a few items.
6. Open **Discover new tools**, paste a supported official vendor's product URL. If the site blocks fetching, show your honest error handling and document a pre-reviewed sample extracted before the demo instead of pretending discovery succeeded.

## Files

```text
app.py                    Flask API, SQLite persistence, matching, upload analysis, discovery
requirements.txt          Python dependencies
Procfile                  Render start command
data/tools.json            Seed catalog - RESEARCH STARTING POINT, verify!
data/sample_customers.csv  Synthetic fake customer transactions
templates/index.html      Six responsive UI panes
static/style.css          Layout and visual design
static/app.js             Browser interactions and API calls
static/sample_customers.csv
```

SQLite tables are created at first start. Seed data is imported once into `tools`. To reload after changing `data/tools.json`, delete your local `tools.db` and restart (only if you don't need existing discovery drafts), or write a proper migration/update script.

## Scoring approach

The current prototype uses an *illustrative* weighted score:

```
0.30 × challenge fit
+ 0.25 × potential existing-software fit
+ 0.20 × pricing/budget evidence (neutral until verified)
+ 0.15 × estimated ease of adoption
+ 0.10 × evidence verification (neutral until verified)
− industry mismatch penalty
```

No scraped review scores or fabricated vendor prices. The next improvement is **hard constraints**: remove tools that are out of budget (once pricing is verified), lack mandatory integrations, or fail privacy requirements; then rank the remaining tools. Show `unknown` rather than `0` when data is missing. For true industry-specific performance rankings, collect test evidence and human assessment data.

## Tool discovery constraints

`POST /api/discover` accepts HTTPS URLs only on a fixed allowlist of official vendor domains, no alternate ports and no redirect following. It extracts visible page metadata and headings. If an API key exists, the LLM summarizes only that evidence. Discovered items go into a `discovered` SQLite table as `Needs review`, not live recommendations. This is a **minimal demonstration**, not web-wide crawling. For production, add domain governance, request size limits during streaming, IP safety checks, terms-of-use compliance, sanitization, and human review. The website content is untrusted input and must never be allowed to override system instructions or execute actions.

## Deploy using Render

1. Put the project in a GitHub repository (do not commit `.env`, `tools.db` or actual customer data).
2. Go to Render → **New** → **Web Service**, connect the GitHub repo.
3. Choose **Python 3**. Build command: `pip install -r requirements.txt`.
4. Start command: `gunicorn app:app`.
5. Set `OPENAI_API_KEY` and `OPENAI_MODEL` as **environment variables**, only if using the AI mode.
6. Test the public `/health` endpoint, profile submission, file upload, and all UI buttons.

**Caution:** Ordinary local SQLite files on stateless hosting may reset across deploys/restarts. This is fine for a seeded demonstration, but **not** for production persistence. Use managed Postgres (e.g. Supabase/Render PostgreSQL) later. Some free hosting may sleep or be limited; test the judge-accessible URL ahead of the deadline.

Official references:
- Flask on Render: https://render.com/docs/deploy-flask
- OpenAI Python SDK: https://developers.openai.com/api/docs/libraries?language=python
- Structured Outputs: https://developers.openai.com/api/docs/guides/structured-outputs

## Known next steps to make it competitive

- Add true source-level evidence (feature, plan, price, date checked, allowed use, integration docs).
- Support *multi-tool stacks* with explicit data flows and a compatibility graph. Today the app recommends individual tools and a **conceptual** workflow only.
- Build a comparison table with alternatives including *no new AI tool*.
- Add free-form problem classification via structured model output and a deterministic schema.
- Add a test suite of 15–20 small-business profiles and publish the pass/fail rates.
- Accessibility check: keyboard navigation, color contrast, aria-live for async results.
- Add manual review UI for discovered candidate tools and a real integration test for one vendor only.

## User data and ethics

Only upload synthetic or properly authorized, minimized data. Do not upload private bank transactions, customer email addresses, or payment details to this public demo. The example repeat-purchase percentage alone does not establish churn, and the ROI estimator is a what-if calculator rather than a measured outcome. No marketing emails are sent. The public /api/discover endpoint has basic limits but is not designed for unrestricted production traffic.
