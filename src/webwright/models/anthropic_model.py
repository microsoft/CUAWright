"""Anthropic (Claude) Messages API model backend."""

from __future__ import annotations

import asyncio
import random
from typing import Any

from webwright.models.base import (
    BaseModel,
    BaseModelConfig,
    OptStr,
    _safe_int,
)

# Anthropic-specific retry limits (independent of OpenAI defaults). The Claude
# Opus org-level ITPM cap can saturate for minutes at high concurrency, so we
# allow many more retries and longer backoffs.
MAX_RATE_LIMIT_RETRIES = 50
MAX_TRANSIENT_GATEWAY_RETRIES = 20
RATE_LIMIT_BACKOFF_MIN_SECONDS = 30.0
RATE_LIMIT_BACKOFF_MAX_SECONDS = 60.0
TRANSIENT_BACKOFF_BASE_SECONDS = 1.5
TRANSIENT_BACKOFF_CAP_SECONDS = 60.0


def _retry_after_seconds(exc: BaseException) -> float | None:
    response = getattr(exc, "response", None)
    if response is None:
        return None
    header = response.headers.get("retry-after") if getattr(response, "headers", None) else None
    if not header:
        return None
    try:
        return max(0.0, float(header))
    except (TypeError, ValueError):
        return None


def _image_source_from_url(image_url: str) -> dict[str, Any]:
    if image_url.startswith("data:"):
        header, _, encoded = image_url.partition(",")
        media_type = header.split(";")[0].removeprefix("data:") or "image/png"
        return {"type": "base64", "media_type": media_type, "data": encoded}
    return {"type": "url", "url": image_url}


def _serialize_anthropic_content_part(part: dict[str, Any]) -> dict[str, Any]:
    if part.get("type") == "input_image":
        return {"type": "image", "source": _image_source_from_url(part.get("image_url", ""))}
    return {"type": "text", "text": part.get("text", "")}


def _serialize_anthropic_messages(
    messages: list[dict[str, Any]],
) -> tuple[str | None, list[dict[str, Any]]]:
    system_chunks: list[str] = []
    serialized: list[dict[str, Any]] = []
    for message in messages:
        role = message["role"]
        if role == "exit":
            continue
        content = message.get("content", "")
        if role == "system":
            if isinstance(content, str):
                if content:
                    system_chunks.append(content)
            else:
                for part in content:
                    if isinstance(part, dict) and part.get("type") != "input_image":
                        text = part.get("text", "")
                        if text:
                            system_chunks.append(text)
            continue

        if isinstance(content, str):
            serialized.append({"role": role, "content": content})
            continue
        parts = [_serialize_anthropic_content_part(p) for p in content if isinstance(p, dict)]
        if parts and all(p.get("type") == "text" for p in parts):
            serialized.append({"role": role, "content": "\n".join(p["text"] for p in parts)})
        else:
            serialized.append({"role": role, "content": parts})

    system_prompt = "\n\n".join(system_chunks) if system_chunks else None
    return system_prompt, serialized


def _extract_anthropic_text(payload: dict[str, Any]) -> str:
    texts: list[str] = []
    for block in payload.get("content") or []:
        if isinstance(block, dict) and block.get("type") == "text":
            text = block.get("text", "")
            if text:
                texts.append(text)
    return "\n".join(texts)


def _usage_from_anthropic_payload(payload: dict[str, Any]) -> dict[str, int]:
    usage = payload.get("usage") or {}
    input_tokens = _safe_int(usage.get("input_tokens"))
    output_tokens = _safe_int(usage.get("output_tokens"))
    cached_input_tokens = _safe_int(usage.get("cache_read_input_tokens"))
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
        "cached_input_tokens": cached_input_tokens,
        "reasoning_output_tokens": 0,
    }


def _metrics_input_from_anthropic(
    system_prompt: str | None, anthropic_messages: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    if system_prompt:
        items.append({"content": [{"type": "input_text", "text": system_prompt}]})
    for msg in anthropic_messages:
        content = msg.get("content", "")
        if isinstance(content, str):
            items.append({"content": [{"type": "input_text", "text": content}]})
            continue
        parts: list[dict[str, Any]] = []
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "text":
                parts.append({"type": "input_text", "text": part.get("text", "")})
            elif part.get("type") == "image":
                parts.append({"type": "input_image"})
        items.append({"content": parts})
    return items


class AnthropicModelConfig(BaseModelConfig):
    model_name: OptStr = "claude-opus-4-7"
    anthropic_api_key: OptStr = ""
    anthropic_endpoint: OptStr = "https://api.anthropic.com/v1/messages"
    anthropic_version: OptStr = "2023-06-01"
    max_output_tokens: int = 8000


class AnthropicModel(BaseModel):
    _API_KEY_FIELD = "anthropic_api_key"
    _ENV_VAR = "ANTHROPIC_API_KEY"
    _LOG_SOURCE = "anthropic"
    _MAX_RATE_LIMIT_RETRIES = MAX_RATE_LIMIT_RETRIES
    _MAX_TRANSIENT_RETRIES = MAX_TRANSIENT_GATEWAY_RETRIES
    _DEFAULT_CONFIG_CLASS = AnthropicModelConfig

    def _request_headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "x-api-key": self.config.anthropic_api_key,
            "anthropic-version": self.config.anthropic_version,
        }

    def _post_url(self) -> str:
        return self.config.anthropic_endpoint

    async def _rate_limit_backoff(self, attempt: int, exc: BaseException) -> None:
        delay = random.uniform(RATE_LIMIT_BACKOFF_MIN_SECONDS, RATE_LIMIT_BACKOFF_MAX_SECONDS)
        retry_after = _retry_after_seconds(exc)
        if retry_after is not None and retry_after > delay:
            delay = min(retry_after, RATE_LIMIT_BACKOFF_MAX_SECONDS * 2)
        await asyncio.sleep(delay)

    async def _transient_backoff(self, attempt: int, exc: BaseException) -> None:
        await asyncio.sleep(
            min(TRANSIENT_BACKOFF_BASE_SECONDS * (2 ** attempt), TRANSIENT_BACKOFF_CAP_SECONDS)
        )

    def _build_payload(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        system_prompt, anth_messages = _serialize_anthropic_messages(messages)
        payload: dict[str, Any] = {
            "model": self.config.model_name,
            "messages": anth_messages,
            "max_tokens": self.config.max_output_tokens,
        }
        if system_prompt:
            payload["system"] = system_prompt
        return payload

    def _request_metrics_input(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        return _metrics_input_from_anthropic(payload.get("system"), payload.get("messages") or [])

    def _extract_text(self, payload: dict[str, Any]) -> str:
        return _extract_anthropic_text(payload)

    def _usage_metrics_from_payload(self, payload: dict[str, Any]) -> dict[str, int]:
        return _usage_from_anthropic_payload(payload)
