"""AI Small Business Solution Lab - hackathon starter.

Start: pip install -r requirements.txt && python app.py
This is a prototype: seeded vendor entries require verification; no vendor integrations
are made and demonstrations are not vendor-generated outputs.
"""
from __future__ import annotations

import io
import json
import os
import re
import sqlite3
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

import pandas as pd
from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request
from advisor import assess, audit, blueprint, reviews_for
from agent.api import bp as agent_bp
from agent.pricing import choose_plan
from agent.schemas import Plan, Usage
from agent.safe_fetch import FetchError, SafeFetcher
from agent.service import AgentService

BASE = Path(__file__).resolve().parent
load_dotenv(BASE / ".env")  # API keys live in the git-ignored .env; real env vars take precedence
DB_PATH = Path(os.environ.get("DB_PATH", str(BASE / "tools.db")))
PROBLEMS = {
    "retention": ["retain", "return", "repeat", "loyal", "churn", "come back", "customers aren't returning"],
    "finance": ["budget", "finance", "cash flow", "expense", "profit", "invoice", "bookkeep", "cost"],
    "marketing": ["market", "advertis", "social", "new customer", "sales", "promotion", "grow"],
    "website": ["website", "site", "online", "seo", "web design"],
    "support": ["question", "support", "customer service", "phone", "chat", "call", "review"],
    "scheduling": ["schedule", "booking", "appointment", "calendar", "missed", "shift"],
    "admin": ["admin", "paperwork", "repetitive", "automation", "manual", "forms", "data entry"],
}
BUDGETS = {"0": 0, "30": 30, "75": 75, "150": 150, "500": 500}
ALLOWED_DISCOVERY_DOMAINS = {
    "squareup.com", "mailchimp.com", "canva.com", "wix.com", "quickbooks.intuit.com",
    "zapier.com", "tidio.com", "calendly.com", "shopify.com", "buffer.com",
    "hubspot.com", "getjobber.com",
}

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with db() as con:
        con.execute("CREATE TABLE IF NOT EXISTS tools (id TEXT PRIMARY KEY, name TEXT, payload TEXT NOT NULL)")
        con.execute("CREATE TABLE IF NOT EXISTS discovered (id INTEGER PRIMARY KEY AUTOINCREMENT, url TEXT, title TEXT, summary TEXT, evidence TEXT, created_at TEXT, status TEXT)")
        con.execute("""CREATE TABLE IF NOT EXISTS review_submissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tool_id TEXT NOT NULL, industry TEXT, company_size TEXT,
            use_case TEXT, rating INTEGER, comment TEXT,
            evidence_url TEXT, created_at TEXT, status TEXT
        )""")
        # Refresh from the catalog file on start so pricing updates reach existing databases.
        catalog = json.loads((BASE / "data" / "tools.json").read_text())
        con.executemany("INSERT OR REPLACE INTO tools (id, name, payload) VALUES (?,?,?)", [
            (tool["id"], tool["name"], json.dumps(tool)) for tool in catalog
        ])


def get_tools():
    with db() as con:
        return [json.loads(row["payload"]) for row in con.execute("SELECT payload FROM tools ORDER BY name")]


def guess_problem(text: str, explicit: str | None):
    if explicit in PROBLEMS:
        return explicit
    lowered = (text or "").lower()
    scores = {p: sum(1 for keyword in words if keyword in lowered) for p, words in PROBLEMS.items()}
    return max(scores, key=scores.get) if max(scores.values()) else "marketing"


def price_estimate(tool, profile):
    """Cheapest catalog plan for one user, with its source; None when the price is unknown."""
    choice = choose_plan([Plan(**p) for p in tool.get("plans", [])], Usage())
    if choice.plan is None or choice.monthly_cost is None:
        return None
    limit = BUDGETS.get(str(profile.get("budget")))
    return {"plan": choice.plan.name, "monthly_cost": choice.monthly_cost, "billing": choice.plan.billing,
            "source_url": tool.get("pricing_source_url", ""), "checked_at": tool.get("pricing_checked_at", ""),
            "within_budget": None if limit is None else choice.monthly_cost <= limit}


def score_tool(tool, profile, problem):
    software = profile.get("software", "None")
    industry = profile.get("industry", "coffee")
    difficulty = tool["difficulty"]
    comfort = profile.get("comfort", "beginner")
    exact = problem in tool["problems"]
    need = 100 if exact else 25
    compatibility = 100 if software in tool["integrations"] else (65 if software == "None" else 40)
    price = price_estimate(tool, profile)
    budget = {True: 100, False: 30, None: 50}[price["within_budget"] if price else None]
    ease = {"easy": 100, "medium": 65, "advanced": 30}[difficulty]
    if comfort == "advanced":
        ease = min(100, ease + 25)
    # Evidence score stays conservative until product facts are verified.
    evidence = 50
    score = round(.30 * need + .25 * compatibility + .20 * budget + .15 * ease + .10 * evidence)
    fit = industry in tool["industries"] or "all" in tool["industries"]
    if not fit:
        score -= 12
    reasons = []
    if exact:
        reasons.append("Addresses your selected business challenge")
    if software in tool["integrations"]:
        reasons.append(f"Potential fit with existing {software} workflow; confirm the integration")
    if fit:
        reasons.append("Relevant to your business category")
    if difficulty == "easy":
        reasons.append("Lower estimated setup complexity")
    return {**tool, "score": max(0, score), "reasons": reasons[:3],
            "verified": False, "price_estimate": price,
            "important_note": "Demo match score. Confirm vendor pricing, AI feature access and integrations before adoption."}


def stack_steps(problem):
    steps = {
        "retention": ["Review customer purchase patterns", "Choose an email/loyalty tool", "Test a consent-based win-back campaign", "Track returning customers"],
        "finance": ["Export expenses and invoices", "Check transaction categories", "Set up a monthly reporting workflow", "Review forecast with a human"],
        "marketing": ["Define a target audience", "Generate and review a sample campaign", "Choose a publishing channel", "Measure campaign results"],
        "website": ["Choose core pages", "Generate a website draft", "Review accuracy and accessibility", "Publish and measure visits"],
        "support": ["Gather common questions", "Create an approved answer library", "Pilot an assistant with human handoff", "Review unresolved requests"],
        "scheduling": ["Map your current booking process", "Connect an appointment tool", "Configure reminders and exceptions", "Track missed appointments"],
        "admin": ["Identify one repetitive workflow", "Map data fields and triggers", "Build and test an automation", "Compare time before and after"],
    }
    return steps[problem]


@app.get("/")
def index():
    return render_template("landing.html", content=landing_content(), year=date.today().year)


def landing_content():
    """Editable, curated marketing copy; never imply unreviewed tools or testimonials are verified."""
    return json.loads((BASE / "data" / "landing.json").read_text(encoding="utf-8"))


@app.get("/solutions")
def solutions():
    return render_template("solutions.html", content=landing_content(), year=date.today().year)


@app.get("/dashboard")
def dashboard():
    return render_template("dashboard.html")


@app.post("/api/recommend")
def recommend():
    profile = request.get_json(silent=True) or {}
    problem = guess_problem(str(profile.get("challengeText", "")), profile.get("problem"))
    tools = [score_tool(t, profile, problem) for t in get_tools()]
    tools.sort(key=lambda t: (t["score"], problem in t["problems"]), reverse=True)
    top = tools[:3]
    for tool in top:
        tool["review_evidence"] = reviews_for(tool)
    return jsonify({"problem": problem, "tools": top,
                    "readiness": assess(profile, problem),
                    "software_audit": audit(profile, problem),
                    "blueprint": blueprint(profile, problem),
                    "steps": stack_steps(problem),
                    "solution": "Start with the best compatible tool and add a second only if a specific workflow needs it.",
                    "disclaimer": "Demo ratings and catalog data require human verification. No vendor account has been connected."})


@app.post("/api/analyze")
def analyze():
    file = request.files.get("file")
    if not file or not file.filename.lower().endswith(".csv"):
        return jsonify({"error": "Upload a CSV with customer_id,date,amount columns."}), 400
    try:
        df = pd.read_csv(io.BytesIO(file.read()), nrows=20000)
        needed = {"customer_id", "date", "amount"}
        if not needed.issubset(df.columns):
            return jsonify({"error": "Required columns: customer_id, date, amount."}), 400
        df["amount"] = pd.to_numeric(df["amount"], errors="coerce")
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df = df.dropna(subset=["customer_id", "date", "amount"])
        if df.empty:
            return jsonify({"error": "No valid purchase rows found."}), 400
        purchases = df.groupby("customer_id").size()
        repeat = int((purchases >= 2).sum())
        customers = int(purchases.size)
        repeat_rate = round(100 * repeat / customers, 1)
        return jsonify({"customers": customers, "orders": len(df),
                        "revenue": round(float(df["amount"].sum()), 2),
                        "average_order": round(float(df["amount"].mean()), 2),
                        "repeat_customers": repeat,
                        "repeat_rate": repeat_rate,
                        "insight": f"{repeat_rate}% of unique customers in this sample placed at least two orders. This is a within-dataset repeat-purchase measure, not a verified retention or churn rate."})
    except Exception:
        return jsonify({"error": "Couldn't read the file. Check its CSV format."}), 400


def ai_text(instructions, content):
    if not os.environ.get("OPENAI_API_KEY"):
        return None
    from openai import OpenAI
    client = OpenAI()
    response = client.responses.create(
        model=os.environ.get("OPENAI_MODEL", "gpt-5.5"),
        instructions=instructions,
        input=content,
    )
    return response.output_text


@app.post("/api/preview")
def preview():
    data = request.get_json(silent=True) or {}
    business = re.sub(r"[^a-zA-Z0-9 '-]", "", str(data.get("business", "your business")))[:55].strip() or "your business"
    offer = "A friendly invitation to return"
    fallback = (f"Subject: We miss seeing you at {business}!\n\n"
                f"It's been a while since your last visit. We'd love to welcome you back soon! "
                f"Drop by whenever you're in the neighborhood.\n\n— The {business} team")
    try:
        generated = ai_text(
            "Write a concise, friendly, truthful sample customer re-engagement email. "
            "Do not claim a discount or customer facts. Include a subject line. No real email is sent.",
            f"Business name: {business}. Goal: {offer}."
        )
    except Exception:
        generated = None
    return jsonify({"content": generated or fallback, "mode": "AI-generated illustration" if generated else "Template illustration",
                    "note": "This preview is produced by this prototype, not by any recommended vendor. Never email customers without appropriate consent."})


@app.post("/api/discover")
def discover():
    """Constrained official-site discovery. Review extracted data before recommendation."""
    payload = request.get_json(silent=True) or {}
    url = str(payload.get("url", "")).strip()
    parsed = urlparse(url)
    domain = (parsed.hostname or "").lower()
    base_domain_allowed = any(domain == d or domain.endswith("." + d) for d in ALLOWED_DISCOVERY_DOMAINS)
    if parsed.scheme != "https" or not base_domain_allowed or parsed.port is not None or parsed.username:
        return jsonify({"error": "For this public demo, choose an HTTPS URL on a supported official vendor domain."}), 400
    try:
        page = SafeFetcher(max_redirects=0, respect_robots=False).fetch(url, allowed_domains=ALLOWED_DISCOVERY_DOMAINS)
    except FetchError as exc:
        return jsonify({"error": str(exc)}), 422
    title, desc, headings = page.title[:130], page.description, page.headings
    summary = (desc or headings or page.text[:1000]).strip()
    if not summary:
        return jsonify({"error": "No useful product details found on the page."}), 422
    try:
        generated = ai_text(
            "Extract the page's explicit product purpose and AI features, if present. "
            "Do not invent pricing, integrations, benefits, or capabilities. "
            "Summarize in under 90 words. Say when an AI feature is not confirmed.",
            f"Official URL: {url}\nPage title: {title}\nExtracted text: {summary}\nHeadings: {headings}"
        )
    except Exception:
        generated = None
    candidate = generated or summary[:400]
    with db() as con:
        con.execute("INSERT INTO discovered(url,title,summary,evidence,created_at,status) VALUES (?,?,?,?,?,?)",
                    (url, title, candidate, summary[:1600], date.today().isoformat(), "Needs review"))
    return jsonify({"title": title, "summary": candidate, "source": url,
                    "status": "Needs review", "mode": "AI extraction" if generated else "Page metadata extraction",
                    "note": "This candidate was extracted from a vendor page but was not independently validated or added to ranked recommendations."})


@app.post("/api/reviews/submit")
def submit_review():
    """Store unverified feedback for moderation. Never publish automatically."""
    payload = request.get_json(silent=True) or {}
    tool_id = str(payload.get("tool_id", ""))
    if tool_id not in {tool["id"] for tool in get_tools()}:
        return jsonify({"error": "Choose a listed tool."}), 400
    comment = str(payload.get("comment", "")).strip()
    if not 30 <= len(comment) <= 1500:
        return jsonify({"error": "Please describe your experience in 30 to 1500 characters."}), 400
    try:
        rating = int(payload.get("rating"))
    except (ValueError, TypeError):
        return jsonify({"error": "Choose a rating from 1 to 5."}), 400
    if rating not in range(1, 6):
        return jsonify({"error": "Choose a rating from 1 to 5."}), 400
    evidence_url = str(payload.get("evidence_url", "")).strip()
    if evidence_url and (not evidence_url.startswith("https://") or len(evidence_url) > 350):
        return jsonify({"error": "Evidence must be an HTTPS link."}), 400
    with db() as con:
        con.execute(
            """INSERT INTO review_submissions
               (tool_id,industry,company_size,use_case,rating,comment,evidence_url,created_at,status)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (tool_id, str(payload.get("industry", ""))[:80],
             str(payload.get("company_size", ""))[:40],
             str(payload.get("use_case", ""))[:100], rating,
             comment, evidence_url, date.today().isoformat(), "Pending moderation")
        )
    return jsonify({"status": "Pending moderation",
                    "message": "Your review was saved for moderation, not published."}), 201

@app.get("/health")
def health():
    return jsonify({"ok": True})


init_db()
app.register_blueprint(agent_bp)
app.extensions["agent_service"] = AgentService.create(DB_PATH)
app.extensions["agent_service"].runner.recover()

if __name__ == "__main__":
    # 5000 is taken by macOS AirPlay Receiver, which answers with "403 Access denied".
    app.run(debug=True, port=int(os.environ.get("PORT", 5050)))
