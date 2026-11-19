"""
Small provider-agnostic LLM client. Owner: Sampurna (taken over from Nooman).

The LLM never decides truth (principle 1). It is used for exactly two jobs:
  1. extracting claims from caption text (app/claims/extract.py)
  2. rewording finding titles/summaries (app/engine/explain.py, guarded)

Provider from ALIBI_LLM_PROVIDER = none (default) | gemini (recommended, free AI Studio key) |
anthropic (PAID, do not use). Model from ALIBI_LLM_MODEL (default per provider in
config.LLM_DEFAULT_MODEL). Budget rule: exactly one call to the configured model, hard 15 s
timeout, no retries, no fallback models. On any refusal or error, callers fall back to
rules/templates.
"""

from __future__ import annotations

import json
import re
from typing import Any

from app import config


class LLMError(Exception):
    """Any LLM failure: not configured, network, timeout, refusal, bad JSON."""


def available() -> bool:
    return config.LLM_PROVIDER in ("anthropic", "gemini") and config.llm_configured()


def model_name() -> str:
    return config.LLM_MODEL or config.LLM_DEFAULT_MODEL.get(config.LLM_PROVIDER, "")


def _anthropic(system: str, prompt: str, max_tokens: int) -> str:
    """PAID provider: kept for completeness, not to be used (budget rule).
    One call to the configured model only: no retries, no server-side or client-side fallback models."""
    import anthropic  # noqa: PLC0415 - optional dependency, only needed when enabled

    model = model_name()
    client = anthropic.Anthropic(
        api_key=config.ANTHROPIC_API_KEY, timeout=config.LLM_TIMEOUT_S, max_retries=0,
    )
    try:
        resp = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
            output_config={"effort": config.LLM_EFFORT},
        )
    except anthropic.APIError as exc:
        raise LLMError(f"anthropic: {type(exc).__name__}: {exc}") from exc
    if resp.stop_reason == "refusal":
        raise LLMError(f"anthropic: {model} declined the request")   # caller falls back to rules
    text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
    if not text.strip():
        raise LLMError(f"anthropic: empty response (stop_reason={resp.stop_reason})")
    return text


def _gemini(system: str, prompt: str, max_tokens: int) -> str:
    from google import genai  # noqa: PLC0415
    from google.genai import types  # noqa: PLC0415

    try:
        client = genai.Client(
            api_key=config.GEMINI_API_KEY,
            http_options=types.HttpOptions(timeout=int(config.LLM_TIMEOUT_S * 1000)),  # milliseconds
        )
        resp = client.models.generate_content(
            model=model_name(),
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=system,
                response_mime_type="application/json",
                max_output_tokens=max_tokens,
            ),
        )
    except Exception as exc:  # noqa: BLE001 - google-genai raises several unrelated types
        raise LLMError(f"gemini: {type(exc).__name__}: {exc}") from exc
    if not (resp.text or "").strip():
        raise LLMError("gemini: empty response")
    return resp.text


def complete(system: str, prompt: str, max_tokens: int = 8000) -> str:
    """One text completion from the configured provider. Raises LLMError."""
    if not available():
        raise LLMError("no LLM provider configured")
    if config.LLM_PROVIDER == "anthropic":
        return _anthropic(system, prompt, max_tokens)
    return _gemini(system, prompt, max_tokens)


def parse_json(text: str) -> Any:
    """Parse a JSON reply, tolerating ```json fences or a little prose around it."""
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fenced:
        text = fenced.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"(\[.*\]|\{.*\})", text, re.S)
        if m:
            try:
                return json.loads(m.group(1))
            except json.JSONDecodeError:
                pass
    raise LLMError("reply is not valid JSON")


def complete_json(system: str, prompt: str, max_tokens: int = 8000) -> Any:
    return parse_json(complete(system, prompt, max_tokens))
