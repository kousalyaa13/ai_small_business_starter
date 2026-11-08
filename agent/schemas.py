"""Structured records passed between the API, the graph and the model."""
from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .capabilities import Capability


class Usage(BaseModel):
    seats: int = Field(1, ge=1, le=500)  # total people needing access
    editors: int | None = Field(None, ge=1, le=500)  # paid editors; remaining people are read-only
    contacts: int | None = Field(None, ge=0, le=10_000_000)
    monthly_volume: int | None = Field(None, ge=0, le=10_000_000)

    @model_validator(mode="after")
    def editors_within_team(self):
        if self.editors is not None and self.editors > self.seats:
            raise ValueError("Editors cannot exceed the number of people needing access.")
        return self


class Intake(BaseModel):
    model_config = ConfigDict(extra="ignore")

    problem_text: str = Field(min_length=10, max_length=2000)
    business_name: str = Field("", max_length=80)
    business_type: str = Field("", max_length=80)  # e.g. "plumbing", "hair salon"; used to judge industry fit
    existing_software: list[str] = Field(default_factory=list, max_length=20)
    monthly_budget: float | None = Field(None, ge=0, le=100_000)
    usage: Usage = Field(default_factory=Usage)
    requirements: list[str] = Field(default_factory=list, max_length=30)

    @model_validator(mode="before")
    @classmethod
    def explicit_numbers_in_description(cls, value):
        """Read clearly stated numbers only when dedicated fields are empty.

        Do not overwrite explicit form entries or infer editor permissions.
        """
        if not isinstance(value, dict):
            return value
        data = dict(value)
        text = str(data.get("problem_text") or "")
        if data.get("monthly_budget") is None:
            m = re.search(r"\b(?:monthly\s+budget|budget\s+(?:of|is|:))\s*\$?([\d,]+(?:\.\d+)?)\s*(?:per\s+month|/month|monthly|mo\b)", text, re.I)
            if m:
                data["monthly_budget"] = float(m.group(1).replace(",", ""))
        usage = dict(data.get("usage") or {})
        if usage.get("seats") is None:
            m = re.search(r"\b(\d{1,3})\s+(?:people|users|employees|staff|team members)\s+(?:need|will|who|to|have)\b", text, re.I)
            if m:
                usage["seats"] = int(m.group(1))
        data["usage"] = usage
        return data

    @field_validator("existing_software", "requirements")
    @classmethod
    def _clean(cls, values):
        return [str(v).strip()[:200] for v in values if str(v).strip()]


# ---- Model-facing schemas: every field required so strict structured output works.

class ClarifyingQuestion(BaseModel):
    id: str
    question: str
    field: Literal["monthly_budget", "monthly_volume", "contacts", "seats", "editors", "business_type", "text"]


class Component(BaseModel):
    id: str
    capability: Capability
    description: str
    constraints: list[str]
    connects: list[str]  # ids of components whose data this component moves
    search_query: str  # what a business owner would type into a search engine to find tools for this


class Decomposition(BaseModel):
    summary: str
    components: list[Component]
    questions: list[ClarifyingQuestion]


LimitKind = Literal["users", "contacts", "bookings", "calls", "messages", "minutes", "tasks"]


class PlanLimit(BaseModel):
    kind: LimitKind  # users = people/seats; the rest are per month except contacts (total stored)
    amount: int
    quote: str  # verbatim text from the page stating the limit


class ExtractedPlan(BaseModel):
    name: str
    monthly_price: float | None
    per_seat: bool
    billing: Literal["monthly", "annual", "free", "unknown"]
    price_quote: str  # verbatim text from the page that states the price
    limits: list[PlanLimit]  # stated usage limits of this plan; [] if none are stated


SetupComplexity = Literal["no-code", "low-code", "developer", "unknown"]


Fit = Literal["built_for_this", "workable", "poor"]


class ExtractedTool(BaseModel):
    is_vendor_product_page: bool
    fits_need: bool  # the product's main purpose fulfils the component being researched
    fit: Fit  # how well its intended purpose matches this need for this type of business
    fit_reason: str  # one short sentence an owner can act on
    small_business_fit: bool  # sold to small businesses, not enterprise-only or another industry
    audience: str  # who the page says the product is for, in a few words
    setup_complexity: SetupComplexity
    name: str
    capabilities: list[Capability]
    description_quote: str
    plans: list[ExtractedPlan]
    api_available: bool | None
    api_quote: str


class FitAssessment(BaseModel):
    key: str  # echoes the pair key given in the prompt
    fit: Fit
    reason: str


class FitAssessments(BaseModel):
    assessments: list[FitAssessment]


class ToolMention(BaseModel):
    name: str
    best_for: str  # the article's short note on what the tool is best for
    url: str | None  # the tool's own website if the article links or names it, else null


class ArticleTools(BaseModel):
    tools: list[ToolMention]


# ---- Application records.

class Plan(BaseModel):
    name: str
    monthly_price: float | None = None
    billing: str = "unknown"
    per_seat: bool = False
    free_viewers: bool = False  # verified read-only access does not consume paid seats
    min_seats: int = Field(1, ge=1, le=500)  # minimum paid members for this plan
    included: dict[str, int | None] = Field(default_factory=dict)
    evidence: str = ""


class Evidence(BaseModel):
    url: str
    quote: str


class Candidate(BaseModel):
    tool_id: str
    name: str
    source: Literal["catalog", "web"]
    vetted: bool
    capabilities: list[str]
    site: str = ""
    description: str = ""
    plans: list[Plan] = Field(default_factory=list)
    pricing_source_url: str = ""
    pricing_checked_at: str = ""
    owned: bool = False
    owned_plan_covers: bool = True  # False when owning the product still needs a paid upgrade
    integrations: list[str] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    deployable_by_platform: bool = False
    audience: str = ""
    setup_complexity: str = "unknown"
    best_for: str = ""
    mentions: int = 0  # how many comparison articles mentioned this tool
    fit: str = "unknown"  # built_for_this | workable | poor, for this need and business type
    fit_reason: str = ""


class Assignment(BaseModel):
    component_id: str
    capability: str
    description: str
    tool_id: str | None
    tool_name: str
    source: Literal["catalog", "web", "owned", "native", "none", "skipped", "existing"]
    vetted: bool
    plan_name: str | None = None
    monthly_cost: float | None = None
    cost_basis: str = ""
    source_url: str = ""
    site_url: str = ""  # vendor's website, separate from the price evidence URL
    pricing_source_url: str = ""
    pricing_checked_at: str = ""
    tool_description: str = ""
    alternatives: list[dict] = Field(default_factory=list)  # other tools that also fit this part
    setup_complexity: str = "unknown"
    best_for: str = ""
    fit: str = "unknown"
    fit_reason: str = ""


class Option(BaseModel):
    id: str
    title: str
    summary: str
    assignments: list[Assignment]
    monthly_total: float | None
    total_is_complete: bool
    within_budget: bool | None
    assumptions: list[str]
    setup_requirements: list[str]
    sources: list[str]
    unvetted_tools: list[str]
    deployable_by_platform: bool = False


class PlanVersion(BaseModel):
    version: int
    requirement_version: int
    summary: str
    monthly_budget: float | None
    usage: Usage
    options: list[Option]
    gaps: list[str]
    research_notes: list[str]
    created_at: str


class Answers(BaseModel):
    answers: dict[str, str] = Field(max_length=10)


class RevisionRequest(BaseModel):
    """Adjust assumptions without losing the original consultation or its plan history."""
    monthly_budget: float | None = Field(None, ge=0, le=100_000)
    seats: int | None = Field(None, ge=1, le=500)
    editors: int | None = Field(None, ge=1, le=500)
    monthly_volume: int | None = Field(None, ge=0, le=10_000_000)
    contacts: int | None = Field(None, ge=0, le=10_000_000)
    problem_text: str | None = Field(None, min_length=10, max_length=2000)


class ResearchRequest(BaseModel):
    """User-requested search for more tools: the listed parts, or every part when none are given."""
    component_ids: list[str] = Field(default_factory=list, max_length=10)
    goal: Literal["cheaper", "better", "both"] = "both"


class SelectionRequest(BaseModel):
    """The owner's pick per need: a tool id from that need's list, "skip" or "have"."""
    selections: dict[str, str] = Field(default_factory=dict, max_length=12)


class ApprovalRequest(BaseModel):
    plan_version: int = Field(ge=1)
    option_id: str = Field(min_length=1, max_length=40)
    selections: dict[str, str] | None = Field(None, max_length=12)  # with option_id "custom"
    max_build_budget: float = Field(0, ge=0, le=10_000)
    idempotency_key: str = Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")
