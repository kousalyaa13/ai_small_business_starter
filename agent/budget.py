"""Per-run spend accounting and hard limits for model calls, searches and page fetches.

Only one worker drives a run at a time (see ``Store.acquire_lease``), so checks
against recorded usage are not raced by concurrent work within the same run.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


def _env_float(name, default=None):
    value = os.environ.get(name)
    return float(value) if value not in (None, "") else default


class BudgetExceeded(Exception):
    pass


@dataclass
class Limits:
    max_searches: int = 6
    max_fetches: int = 12
    max_model_calls: int = 20
    max_run_tokens: int = 200_000
    max_run_usd: float | None = 1.00
    # Unit prices are configuration, not assumptions baked into code. When unset,
    # spend is recorded as unknown and the token limit is the binding control.
    price_in_per_mtok: float | None = None
    price_cached_in_per_mtok: float | None = None
    price_out_per_mtok: float | None = None
    price_per_search: float | None = None
    price_per_fetch: float | None = None

    @classmethod
    def from_env(cls):
        return cls(
            max_searches=int(_env_float("AGENT_MAX_SEARCHES", 6)),
            max_fetches=int(_env_float("AGENT_MAX_FETCHES", 12)),
            max_model_calls=int(_env_float("AGENT_MAX_MODEL_CALLS", 20)),
            max_run_tokens=int(_env_float("AGENT_MAX_RUN_TOKENS", 200_000)),
            max_run_usd=_env_float("AGENT_MAX_RUN_USD", 1.00),
            price_in_per_mtok=_env_float("AGENT_PRICE_IN_PER_MTOK"),
            price_cached_in_per_mtok=_env_float("AGENT_PRICE_CACHED_IN_PER_MTOK"),
            price_out_per_mtok=_env_float("AGENT_PRICE_OUT_PER_MTOK"),
            price_per_search=_env_float("AGENT_PRICE_PER_SEARCH"),
            price_per_fetch=_env_float("AGENT_PRICE_PER_FETCH"),
        )


class Meter:
    def __init__(self, store, run_id, project_id, limits: Limits):
        self.store, self.run_id, self.project_id, self.limits = store, run_id, project_id, limits

    def totals(self):
        usage = self.store.usage_totals(self.run_id)
        model = usage.get("model", {})
        return {
            "model_calls": model.get("calls", 0) or 0,
            "tokens": (model.get("input_tokens") or 0) + (model.get("output_tokens") or 0),
            "cached_input_tokens": model.get("cached_input_tokens") or 0,
            "searches": usage.get("search", {}).get("calls", 0) or 0,
            "fetches": usage.get("fetch", {}).get("calls", 0) or 0,
            "est_usd": round(sum((u.get("est_usd") or 0) for u in usage.values()), 4),
            "spend_complete": not any((u.get("unpriced") or 0) for u in usage.values()),
        }

    def ensure(self, kind):
        t, lim = self.totals(), self.limits
        if t["tokens"] >= lim.max_run_tokens:
            raise BudgetExceeded("token limit reached")
        if lim.max_run_usd is not None and t["est_usd"] >= lim.max_run_usd:
            raise BudgetExceeded("spend limit reached")
        if kind == "model" and t["model_calls"] >= lim.max_model_calls:
            raise BudgetExceeded("model call limit reached")
        if kind == "search" and t["searches"] >= lim.max_searches:
            raise BudgetExceeded("search limit reached")
        if kind == "fetch" and t["fetches"] >= lim.max_fetches:
            raise BudgetExceeded("page fetch limit reached")

    def _model_usd(self, usage):
        if usage.provider == "stub":
            return 0.0
        lim = self.limits
        if lim.price_in_per_mtok is None or lim.price_out_per_mtok is None:
            return None
        cached_price = lim.price_cached_in_per_mtok if lim.price_cached_in_per_mtok is not None else lim.price_in_per_mtok
        fresh = usage.input_tokens - usage.cached_input_tokens
        return round((fresh * lim.price_in_per_mtok + usage.cached_input_tokens * cached_price
                      + usage.output_tokens * lim.price_out_per_mtok) / 1_000_000, 6)

    def model_call(self, model, task, instructions, content, schema):
        self.ensure("model")
        result, usage = model.structured(task, instructions, content, schema)
        self.store.record_usage(self.run_id, self.project_id, "model", est_usd=self._model_usd(usage),
                                provider=usage.provider, model=usage.model, task=task,
                                input_tokens=usage.input_tokens, cached_input_tokens=usage.cached_input_tokens,
                                output_tokens=usage.output_tokens)
        return result

    def record_search(self, provider):
        self.store.record_usage(self.run_id, self.project_id, "search", provider=provider,
                                est_usd=0.0 if provider == "fake" else self.limits.price_per_search)

    def record_fetch(self, provider="direct"):
        est = 0.0 if provider in ("direct", "fake") else self.limits.price_per_fetch
        self.store.record_usage(self.run_id, self.project_id, "fetch", provider=provider, est_usd=est)
