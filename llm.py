"""Provider adapter for free-tier LLM calls (CLAUDE.md sections 2, 3, 13).

Only this module imports a provider SDK (`openai`, used against an
OpenAI-compatible endpoint -- Gemini today, Groq is wired but unused per
the project's decision to stay on Gemini's free tier only). Every other
module (drafter.py, agent.py, ...) calls through `call()` / `call_json()`.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from openai import APIStatusError, OpenAI, RateLimitError

load_dotenv(dotenv_path=".env")

CACHE_DIR = Path("data/llm_cache")
RETRYABLE_STATUS_CODES = {429, 503}


class LLMError(Exception):
    """Raised when a model call fails after its retry (and fallback, if configured)."""


# ── Usage tracking (for test/evaluate.py's token/cost report, section 11) ──
# A simple module-level accumulator: every call(), cached or fresh, adds its
# usage here. reset_usage_totals() at the start of a run, get_usage_totals()
# at the end -- avoids threading usage objects through every function that
# calls an LLM just to report a run-level total.

_usage_log: list[dict] = []


def reset_usage_totals() -> None:
    _usage_log.clear()


def _record_usage(provider: str, model: str, prompt_tokens: int, completion_tokens: int, total_tokens: int, cached: bool) -> None:
    _usage_log.append({
        "provider": provider, "model": model,
        "prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens,
        "total_tokens": total_tokens, "cached": cached,
    })


def get_usage_totals() -> dict:
    """{"calls": int, "cached_calls": int, "prompt_tokens": int,
    "completion_tokens": int, "total_tokens": int, "by_model": {model: {...}}}."""
    totals = {"calls": len(_usage_log), "cached_calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "by_model": {}}
    for entry in _usage_log:
        if entry["cached"]:
            totals["cached_calls"] += 1
        totals["prompt_tokens"] += entry["prompt_tokens"]
        totals["completion_tokens"] += entry["completion_tokens"]
        totals["total_tokens"] += entry["total_tokens"]
        by_model = totals["by_model"].setdefault(
            entry["model"], {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        )
        by_model["calls"] += 1
        by_model["prompt_tokens"] += entry["prompt_tokens"]
        by_model["completion_tokens"] += entry["completion_tokens"]
        by_model["total_tokens"] += entry["total_tokens"]
    return totals


@dataclass
class LLMResponse:
    content: str
    model: str
    provider: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cached: bool = False


class RateLimiter:
    """A sliding-window limiter: at most `max_per_minute` calls in any
    rolling 60-second window. One instance per provider, shared module-wide
    -- fine for a single-process Streamlit app / CLI prototype."""

    def __init__(self, max_per_minute: int) -> None:
        self.max_per_minute = max_per_minute
        self._timestamps: list[float] = []

    def wait_if_needed(self) -> None:
        now = time.monotonic()
        self._timestamps = [t for t in self._timestamps if now - t < 60]
        if len(self._timestamps) >= self.max_per_minute:
            sleep_for = 60 - (now - self._timestamps[0]) + 0.05
            if sleep_for > 0:
                time.sleep(sleep_for)
            now = time.monotonic()
            self._timestamps = [t for t in self._timestamps if now - t < 60]
        self._timestamps.append(time.monotonic())


_rate_limiters: dict[str, RateLimiter] = {}


def _rate_limiter_for(provider: str, max_per_minute: int) -> RateLimiter:
    if provider not in _rate_limiters:
        _rate_limiters[provider] = RateLimiter(max_per_minute)
    return _rate_limiters[provider]


def _cache_key(provider: str, model: str, messages: list[dict], json_mode: bool) -> str:
    payload = json.dumps(
        {"provider": provider, "model": model, "messages": messages, "json_mode": json_mode},
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _cache_path(key: str) -> Path:
    return CACHE_DIR / f"{key}.json"


def _read_cache(key: str) -> Optional[dict]:
    path = _cache_path(key)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _write_cache(key: str, data: dict) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _cache_path(key).write_text(json.dumps(data, indent=2), encoding="utf-8")


def _client_for(provider: str, llm_config: dict) -> OpenAI:
    provider_cfg = llm_config["providers"][provider]
    api_key = os.environ.get(provider_cfg["api_key_env"])
    if not api_key:
        raise LLMError(f"{provider_cfg['api_key_env']} is not set in .env")
    return OpenAI(api_key=api_key, base_url=provider_cfg["base_url"])


def _is_retryable(exc: Exception) -> bool:
    if isinstance(exc, RateLimitError):
        return True
    if isinstance(exc, APIStatusError) and getattr(exc, "status_code", None) in RETRYABLE_STATUS_CODES:
        return True
    return False


def call(
    llm_config: dict,
    role: str,
    system_prompt: str,
    user_prompt: str,
    json_mode: bool = False,
    use_cache: Optional[bool] = None,
    max_tokens: int = 1500,
) -> LLMResponse:
    """Makes one model call through the configured provider.

    role: "fast" or "writing" -> looked up as providers[provider][f"{role}_model"].
    Behaviour (section 13): rate-limited to max_requests_per_minute, one
    retry after a 429/503 with a short pause, then -- only if
    fallback_on_quota is true -- the same request is tried on the other
    provider. Every successful response is cached on disk, keyed by the
    exact request, so repeated test/demo runs cost no quota.
    """
    provider_name = llm_config["provider"]
    use_cache = llm_config.get("cache_responses", True) if use_cache is None else use_cache
    model_key = f"{role}_model"

    providers_to_try = [provider_name]
    if llm_config.get("fallback_on_quota"):
        other = "groq" if provider_name == "gemini" else "gemini"
        providers_to_try.append(other)

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    last_error: Optional[Exception] = None
    for provider in providers_to_try:
        model = llm_config["providers"][provider][model_key]
        cache_key = _cache_key(provider, model, messages, json_mode)

        if use_cache:
            cached = _read_cache(cache_key)
            if cached is not None:
                _record_usage(
                    provider, model, cached["prompt_tokens"], cached["completion_tokens"],
                    cached["total_tokens"], cached=True,
                )
                return LLMResponse(**cached, cached=True)

        limiter = _rate_limiter_for(provider, llm_config["max_requests_per_minute"])

        for attempt in range(2):  # one retry, same provider
            limiter.wait_if_needed()
            try:
                client = _client_for(provider, llm_config)
                kwargs: dict = {"model": model, "messages": messages, "max_tokens": max_tokens}
                if json_mode:
                    kwargs["response_format"] = {"type": "json_object"}
                response = client.chat.completions.create(**kwargs)
                content = response.choices[0].message.content or ""
                usage = response.usage
                result = {
                    "content": content,
                    "model": model,
                    "provider": provider,
                    "prompt_tokens": usage.prompt_tokens if usage else 0,
                    "completion_tokens": usage.completion_tokens if usage else 0,
                    "total_tokens": usage.total_tokens if usage else 0,
                }
                if use_cache:
                    _write_cache(cache_key, result)
                _record_usage(
                    provider, model, result["prompt_tokens"], result["completion_tokens"],
                    result["total_tokens"], cached=False,
                )
                return LLMResponse(**result, cached=False)
            except Exception as e:  # noqa: BLE001 -- provider SDK can raise several types
                last_error = e
                if attempt == 0 and _is_retryable(e):
                    time.sleep(5)
                    continue
                break  # not retryable, or retry exhausted -> try next provider (if any)

    raise LLMError(f"All providers failed for role={role!r}. Last error: {last_error}")


def call_json(
    llm_config: dict,
    role: str,
    system_prompt: str,
    user_prompt: str,
    max_tokens: int = 1500,
) -> tuple[dict, LLMResponse]:
    """Like call(), parsed as JSON. One extra retry (bypassing the cache)
    if the first response is not valid JSON (section 13: 'validated JSON
    output, error handling with a single retry')."""
    response = call(llm_config, role, system_prompt, user_prompt, json_mode=True, max_tokens=max_tokens)
    try:
        return json.loads(response.content), response
    except json.JSONDecodeError:
        response = call(
            llm_config, role, system_prompt, user_prompt,
            json_mode=True, use_cache=False, max_tokens=max_tokens,
        )
        try:
            return json.loads(response.content), response
        except json.JSONDecodeError as e:
            raise LLMError(
                f"Model did not return valid JSON after retry: {response.content[:300]!r}"
            ) from e
