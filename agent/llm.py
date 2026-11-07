"""Provider-agnostic model access with per-task model routing and usage accounting.

``OpenAIModel`` is the first real adapter. ``StubModel`` is deterministic and offline;
it is used in tests and whenever no API key is configured.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Protocol, TypeVar

from pydantic import BaseModel

from .capabilities import KEYWORDS, VOLUME_SENSITIVE
from .schemas import ClarifyingQuestion, Component, Decomposition, ExtractedPlan, ExtractedTool

T = TypeVar("T", bound=BaseModel)


@dataclass
class ModelUsage:
    provider: str
    model: str
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0


class ModelClient(Protocol):
    name: str

    def structured(self, task: str, instructions: str, content: str, schema: type[T]) -> tuple[T, ModelUsage]:
        ...


OPENROUTER_DEFAULTS = {"decompose": "openai/gpt-5-mini", "extract": "openai/gpt-5-nano"}


def model_for(task: str, provider: str = "openai") -> str:
    """Cheap models for extraction, stronger ones for decomposition — all via config."""
    if provider == "openrouter":
        default = os.environ.get("OPENROUTER_MODEL") or OPENROUTER_DEFAULTS.get(task, "openai/gpt-5-mini")
    else:
        default = os.environ.get("OPENAI_MODEL", "gpt-5.5")
    return os.environ.get(f"AGENT_MODEL_{task.upper()}", default)


class OpenAIModel:
    name = "openai"

    def __init__(self, client=None):
        from openai import OpenAI
        self.client = client or OpenAI()

    def structured(self, task, instructions, content, schema):
        model = model_for(task)
        # Static instructions come first so provider-side prompt caching can reuse them.
        response = self.client.responses.parse(model=model, instructions=instructions,
                                               input=content, text_format=schema)
        usage = getattr(response, "usage", None)
        details = getattr(usage, "input_tokens_details", None)
        return response.output_parsed, ModelUsage(
            provider=self.name, model=model,
            input_tokens=getattr(usage, "input_tokens", 0) or 0,
            cached_input_tokens=getattr(details, "cached_tokens", 0) or 0,
            output_tokens=getattr(usage, "output_tokens", 0) or 0,
        )


class OpenRouterModel:
    """OpenRouter via its OpenAI-compatible Chat Completions endpoint with JSON-schema output."""

    name = "openrouter"
    base_url = "https://openrouter.ai/api/v1"

    def __init__(self, client=None):
        from openai import OpenAI
        self.client = client or OpenAI(api_key=os.environ["OPENROUTER_API_KEY"], base_url=self.base_url)

    def structured(self, task, instructions, content, schema):
        model = model_for(task, self.name)
        response = self.client.chat.completions.parse(
            model=model,
            messages=[{"role": "system", "content": instructions}, {"role": "user", "content": content}],
            response_format=schema,
            # Only route to provider endpoints that honour structured output.
            extra_body={"provider": {"require_parameters": True}},
        )
        message = response.choices[0].message
        if message.parsed is None:
            raise ValueError(f"{model} returned no structured output: {message.refusal or 'empty response'}")
        usage = getattr(response, "usage", None)
        details = getattr(usage, "prompt_tokens_details", None)
        return message.parsed, ModelUsage(
            provider=self.name, model=model,
            input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            cached_input_tokens=getattr(details, "cached_tokens", 0) or 0,
            output_tokens=getattr(usage, "completion_tokens", 0) or 0,
        )


PRICE_RE = re.compile(r"(?:US)?\$\s?(\d{1,4}(?:\.\d{1,2})?)\s*(?:USD)?\s*(?:/|per|a)\s*(?:mo\b|month)", re.I)


class StubModel:
    """Keyword decomposition and regex extraction; good enough to exercise the flow."""

    name = "stub"

    def structured(self, task, instructions, content, schema):
        if schema is Decomposition:
            result = self._decompose(content)
        elif schema is ExtractedTool:
            result = self._extract(content)
        else:
            raise ValueError(f"Stub model cannot produce {schema.__name__}")
        return result, ModelUsage(provider=self.name, model="stub", input_tokens=len(content) // 4,
                                  output_tokens=len(result.model_dump_json()) // 4)

    @staticmethod
    def _field(content, label):
        match = re.search(rf"^{label}: (.*)$", content, re.M)
        return match.group(1).strip() if match else ""

    def _decompose(self, content):
        problem = self._field(content, "Problem").lower()
        found = [cap for cap, words in KEYWORDS.items() if any(w in problem for w in words)]
        if not found:
            found = ["other"]
        components = []
        constraints = ["Avoid duplicate records (match on email)"] if re.search(r"duplicat", problem) else []
        for i, cap in enumerate(found, start=1):
            components.append(Component(id=f"c{i}", capability=cap, description=f"Handle: {cap.replace('_', ' ')}",
                                        constraints=constraints if cap == "crm_contacts" else [], connects=[]))
        # "When X happens, add/send to Y" between two systems implies a connector.
        if len(components) >= 2 and re.search(r"\b(whenever|when|automatic|sync|add them|send them)\b", problem):
            components.append(Component(id=f"c{len(components) + 1}", capability="workflow_automation",
                                        description="Move each new record between the apps automatically",
                                        constraints=constraints, connects=[c.id for c in components[:2]]))
        questions = []
        if self._field(content, "Monthly budget") in ("", "not provided"):
            questions.append(ClarifyingQuestion(id="q_budget", field="monthly_budget",
                                                question="What is the most you want to spend per month on new software?"))
        if (self._field(content, "Monthly volume") in ("", "not provided")
                and any(c.capability in VOLUME_SENSITIVE for c in components)):
            questions.append(ClarifyingQuestion(id="q_volume", field="monthly_volume",
                                                question="About how many new leads, contacts or form submissions do you expect each month?"))
        return Decomposition(summary=f"{len(components)} part(s) identified from your description.",
                             components=components, questions=questions)

    def _extract(self, content):
        name = self._field(content, "Page title").split("|")[0].split(" - ")[0].strip() or "Unknown tool"
        text = content.split("Page text:", 1)[-1]
        caps = [cap for cap, words in KEYWORDS.items() if any(w in text.lower() for w in words)]
        plans = []
        for match in PRICE_RE.finditer(text):
            start = max(0, match.start() - 40)
            quote = text[start:match.end()].strip()
            plans.append(ExtractedPlan(name=f"Plan {len(plans) + 1}", monthly_price=float(match.group(1)),
                                       per_seat=False, billing="monthly", price_quote=quote))
            if len(plans) == 3:
                break
        description = " ".join(text.split()[:20])
        return ExtractedTool(is_vendor_product_page=bool(caps), name=name, capabilities=caps,
                             description_quote=description, plans=plans, api_available=None, api_quote="")


def default_model() -> ModelClient:
    """AGENT_PROVIDER (openai | openrouter | stub) wins; otherwise the first configured key."""
    forced = os.environ.get("AGENT_PROVIDER", "").strip().lower()
    if forced:
        providers = {"openai": OpenAIModel, "openrouter": OpenRouterModel, "stub": StubModel}
        if forced not in providers:
            raise ValueError(f"AGENT_PROVIDER must be one of {', '.join(providers)}")
        return providers[forced]()
    if os.environ.get("OPENAI_API_KEY"):
        return OpenAIModel()
    if os.environ.get("OPENROUTER_API_KEY"):
        return OpenRouterModel()
    return StubModel()
