"""Explainable business readiness, current-software audit and workflow planning.
Research prototype only; no vendor account or AI integration is actually connected.
"""
WORKFLOWS = {
    "retention": ("Increase repeat customers",
        ["Export customer records manually","Guess which customers stopped visiting","Write promotions from scratch","Rarely measure return visits"],
        ["Review POS purchase patterns","Identify potentially inactive customers","Draft an approved, opt-in campaign","Measure returning customers"],
        "Repeat-purchase percentage"),
    "marketing": ("Find new customers",
        ["Create social posts manually","Post inconsistently","Guess what resonates","Track results irregularly"],
        ["Define your audience","Draft and approve a campaign","Publish through an existing channel","Track inquiries and conversions"],
        "Qualified inquiries"),
    "finance": ("Understand spending and cash flow",
        ["Collect transaction exports","Categorize expenses manually","Review balances late","Make decisions without a forecast"],
        ["Review transaction data","Validate categories","Prepare a draft trend analysis","Approve changes with a human"],
        "Monthly reporting hours"),
    "website": ("Improve online visibility",
        ["Use outdated website information","Answer repeat basic questions","Update content by hand","Measure little traffic"],
        ["Collect verified business facts","Draft pages with an AI builder","Review accessibility and accuracy","Publish and measure leads"],
        "Website inquiries"),
    "support": ("Answer customers faster",
        ["Read incoming messages","Search manually for answers","Reply when staff are free","Resolve complex cases ad hoc"],
        ["Create an approved FAQ","Pilot responses with AI","Escalate uncertain cases to staff","Track resolution times"],
        "First-response time"),
    "scheduling": ("Reduce missed appointments",
        ["Customers call","Staff enter bookings","Manually remind customers","Track no-shows inconsistently"],
        ["Offer digital booking","Check availability","Send automated reminders","Measure no-shows"],
        "Missed appointments"),
    "admin": ("Save administrative time",
        ["Copy data between apps","Check entries by hand","Email status updates","Reconcile mistakes"],
        ["Document triggers and fields","Validate inputs","Run a limited automation","Monitor exceptions and hours saved"],
        "Administrative hours"),
}
SOFTWARE = {
    "Square": ("Sales and customer records may already support parts of your goal.","Check your current Square plan's built-in marketing and customer tools first."),
    "Shopify": ("Existing store features may cover customer and marketing tasks.","Inspect Shopify's current plan features before adding another app."),
    "QuickBooks": ("Accounting reports and invoicing may already answer your question.","Start with existing bookkeeping reports and verify data access."),
    "Google Sheets": ("A good spreadsheet may be enough for a first pilot.","Try a validated template and simple formulas before subscribing."),
    "Wix": ("Your website builder may include useful content and customer features.","Review your current Wix plan and integrations."),
    "None": ("A manual checklist may be the right low-cost first step.","Confirm what data you need before buying software."),
}

def assess(profile, problem):
    software = profile.get("software", "None")
    data = profile.get("data_available", "unsure")
    consent = profile.get("consent", "unsure")
    score = 30
    positives = ["You identified a business goal and a software budget."]
    blockers, next_steps = [], []
    if software != "None":
        score += 20
        positives.append("You already have a tool to evaluate before adding another.")
    else:
        next_steps.append("List all software and spreadsheets you currently use.")
    if data == "yes":
        score += 25
        positives.append("You indicated relevant business records are available.")
    elif data == "some":
        score += 12
        next_steps.append("Check whether your records are accurate and complete.")
    else:
        blockers.append("Relevant data availability isn't confirmed.")
        next_steps.append("Gather a small sample dataset for testing.")
    if problem in ("retention", "marketing"):
        if consent == "yes":
            score += 15
            positives.append("You indicated you have opt-in records for customer outreach.")
        else:
            score -= 10
            blockers.append("Marketing consent hasn't been confirmed.")
            next_steps.append("Verify customer messaging permission and unsubscribe handling.")
    else:
        score += 15
    if profile.get("comfort") != "beginner":
        score += 10
    else:
        next_steps.append("Choose a guided setup with human oversight.")
    return {"score": max(0,min(score,100)), "positives": positives,
        "blockers": blockers, "next_steps": next_steps,
        "status": "Limited pilot may be reasonable" if score >= 75 and not blockers else "Prepare before connecting tools",
        "note": "Illustrative readiness checklist based only on self-reported information, not an audit."}

def audit(profile, problem):
    name = profile.get("software", "None")
    current, first = SOFTWARE.get(name, SOFTWARE["None"])
    return {"software": name, "existing_value":current, "try_first":first,
       "future": "Compare a new solution only if current capabilities cannot meet the goal.",
       "note": "Existing features and integrations are not verified; check your actual subscription."}

def blueprint(profile, problem):
    goal,before,after,kpi=WORKFLOWS.get(problem,WORKFLOWS["marketing"])
    labels=["Existing system / records","Check quality and permissions","Recommended AI or automation","Human review","Measure outcomes"]
    notes=["Existing data source; not connected","Validation before using data","Proposed only; check official integrations","Owner remains responsible","Measure the pilot KPI"]
    return {"goal":goal,"before":before,"after":after,"metric":kpi,
        "nodes":[{"title":title,"detail":detail} for title,detail in zip(labels,notes)],
        "disclaimer":"Architecture is illustrative; no live integration has been deployed."}

def reviews_for(tool):
    """Never manufacture customer quotes, ratings, or claimed review outcomes."""
    return {"directory_name":"G2","search_url":"https://www.g2.com/search?query=" + tool["name"].replace(" ","%20"),
        "note":"External review search only. Verify reviewer size, industry and use case on the source."}
