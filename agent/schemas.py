"""Structured records passed between the API, the graph and the model."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .capabilities import Capability


class Usage(BaseModel):
    seats: int = Field(1, ge=1, le=500)
    contacts: int | None = Field(None, ge=0, le=10_000_000)
    monthly_volume: int | None = Field(None, ge=0, le=10_000_000)


class Intake(BaseModel):
    model_config = ConfigDict(extra="ignore")

    problem_text: str = Field(min_length=10, max_length=2000)
    business_name: str = Field("", max_length=80)
    business_type: str = Field("", max_length=80)  # e.g. "plumbing", "hair salon"; used to judge industry fit
    existing_software: list[str] = Field(default_factory=list, max_length=20)
    monthly_budget: float | None = Field(None, ge=0, le=100_000)
    usage: Usage = Field(default_factory=Usage)
    requirements: list[str] = Field(default_factory=list, max_length=30)

    @field_validator("existing_software", "requirements")
    @classmethod
    def _clean(cls, values):
        return [str(v).strip()[:200] for v in values if str(v).strip()]


# ---- Model-facing schemas: every field required so strict structured output works.

class ClarifyingQuestion(BaseModel):
    id: str
    question: str
    field: Literal["monthly_budget", "monthly_volume", "contacts", "seats", "text"]


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


class ExtractedPlan(BaseModel):
    name: str
    monthly_price: float | None
    per_seat: bool
    billing: Literal["monthly", "annual", "free", "unknown"]
    price_quote: str  # verbatim text from the page that states the price


SetupComplexity = Literal["no-code", "low-code", "developer", "unknown"]


class ExtractedTool(BaseModel):
    is_vendor_product_page: bool
    fits_need: bool  # the product's main purpose fulfils the component being researched
    small_business_fit: bool  # sold to small businesses, not enterprise-only or another industry
    audience: str  # who the page says the product is for, in a few words
    setup_complexity: SetupComplexity
    name: str
    capabilities: list[Capability]
    description_quote: str
    plans: list[ExtractedPlan]
    api_available: bool | None
    api_quote: str


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


class Assignment(BaseModel):
    component_id: str
    capability: str
    description: str
    tool_id: str | None
    tool_name: str
    source: Literal["catalog", "web", "owned", "native", "none"]
    vetted: bool
    plan_name: str | None = None
    monthly_cost: float | None = None
    cost_basis: str = ""
    source_url: str = ""
    alternatives: list[dict] = Field(default_factory=list)  # other tools that also fit this part
    setup_complexity: str = "unknown"
    best_for: str = ""


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


class ApprovalRequest(BaseModel):
    plan_version: int = Field(ge=1)
    option_id: str = Field(min_length=1, max_length=40)
    max_build_budget: float = Field(0, ge=0, le=10_000)
    idempotency_key: str = Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")
