"""OpenRouter chat completions model backend."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from webwright.models.base import (
    BaseModel,
    BaseModelConfig,
    OptStr,
    _safe_int,
)

__all__ = [
    "OpenRouterModel",
    "OpenRouterModelConfig",
]


def _serialize_chat_content_part(part: dict[str, Any]) -> dict[str, Any] | None:
    part_type = part.get("type")
    if part_type in {"input_text", "output_text"}:
        return {"type": "text", "text": str(part.get("text", "") or "")}
    if part_type == "input_image":
        return {
            "type": "image_url",
            "image_url": {
                "url": str(part.get("image_url", "") or ""),
                "detail": str(part.get("detail", "high") or "high"),
            },
        }
    return None


def _serialize_chat_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    serialized: list[dict[str, Any]] = []
    for message in messages:
        role = message["role"]
        if role == "exit":
            continue
        mapped_role = "system" if role == "system" else ("assistant" if role == "assistant" else "user")
        content = message.get("content", "")
        if isinstance(content, str):
            serialized.append({"role": mapped_role, "content": content})
            continue
        parts = [
            serialized_part
            for part in content
            if isinstance(part, dict)
            for serialized_part in [_serialize_chat_content_part(part)]
            if serialized_part is not None
        ]
        if mapped_role == "assistant" or all(part.get("type") == "text" for part in parts):
            serialized.append(
                {
                    "role": mapped_role,
                    "content": "\n".join(str(part.get("text", "") or "") for part in parts),
                }
            )
        else:
            serialized.append({"role": mapped_role, "content": parts})
    return serialized


def _metrics_input_from_chat_messages(chat_messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    metrics_input: list[dict[str, Any]] = []
    for message in chat_messages:
        content = message.get("content", "")
        if isinstance(content, str):
            metrics_input.append({"content": [{"type": "input_text", "text": content}]})
            continue
        parts: list[dict[str, Any]] = []
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "text":
                parts.append({"type": "input_text", "text": str(part.get("text", "") or "")})
            elif part.get("type") == "image_url":
                parts.append({"type": "input_image"})
        metrics_input.append({"content": parts})
    return metrics_input


def _extract_chat_completions_text(payload: dict[str, Any]) -> str:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    first_choice = choices[0]
    if not isinstance(first_choice, dict):
        return ""
    message = first_choice.get("message", {})
    if not isinstance(message, dict):
        return ""
    content = message.get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            str(part.get("text", "") or "")
            for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        )
    return ""


def _usage_metrics_from_chat_completions(payload: dict[str, Any]) -> dict[str, int]:
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        usage = {}
    return {
        "input_tokens": _safe_int(usage.get("prompt_tokens")),
        "output_tokens": _safe_int(usage.get("completion_tokens")),
        "total_tokens": _safe_int(usage.get("total_tokens")),
        "cached_input_tokens": 0,
        "reasoning_output_tokens": 0,
    }


def _endpoint_host(endpoint: str) -> str:
    return (urlparse(endpoint).hostname or "").lower()


def _is_openai_endpoint(endpoint: str) -> bool:
    return _endpoint_host(endpoint) == "api.openai.com"


def _is_openrouter_endpoint(endpoint: str) -> bool:
    return _endpoint_host(endpoint).endswith("openrouter.ai")


class OpenRouterModelConfig(BaseModelConfig):
    model_name: OptStr = "openai/gpt-5.4"
    openrouter_api_key: OptStr = ""
    openrouter_endpoint: OptStr = "https://openrouter.ai/api/v1/chat/completions"
    http_referer: OptStr = ""
    app_title: OptStr = ""
    provider_require_parameters: bool = True


class OpenRouterModel(BaseModel):
    _API_KEY_FIELD = "openrouter_api_key"
    _ENV_VAR = "OPENROUTER_API_KEY"
    _LOG_SOURCE = "openrouter"
    _MAX_RATE_LIMIT_RETRIES = 5
    _MAX_TRANSIENT_RETRIES = 5
    _DEFAULT_CONFIG_CLASS = OpenRouterModelConfig

    def _request_headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.config.openrouter_api_key}",
        }
        if _is_openrouter_endpoint(self.config.openrouter_endpoint) and self.config.http_referer:
            headers["HTTP-Referer"] = self.config.http_referer
        if _is_openrouter_endpoint(self.config.openrouter_endpoint) and self.config.app_title:
            headers["X-Title"] = self.config.app_title
        return headers

    def _post_url(self) -> str:
        return self.config.openrouter_endpoint

    def _build_payload(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.config.model_name,
            "messages": _serialize_chat_messages(messages),
            "stream": False,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "playwright_step",
                    "strict": True,
                    "schema": self._response_schema(),
                },
            },
        }
        if _is_openai_endpoint(self.config.openrouter_endpoint) and self.config.model_name.startswith("gpt-5"):
            payload["max_completion_tokens"] = self.config.max_output_tokens
        else:
            payload["max_tokens"] = self.config.max_output_tokens
        if self.config.provider_require_parameters and _is_openrouter_endpoint(self.config.openrouter_endpoint):
            payload["provider"] = {"require_parameters": True}
        return payload

    def _build_text_payload(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.config.model_name,
            "messages": _serialize_chat_messages(messages),
            "stream": False,
        }
        if _is_openai_endpoint(self.config.openrouter_endpoint) and self.config.model_name.startswith("gpt-5"):
            payload["max_completion_tokens"] = self.config.max_output_tokens
        else:
            payload["max_tokens"] = self.config.max_output_tokens
        if self.config.provider_require_parameters and _is_openrouter_endpoint(self.config.openrouter_endpoint):
            payload["provider"] = {"require_parameters": True}
        return payload

    def _request_metrics_input(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        return _metrics_input_from_chat_messages(payload.get("messages") or [])

    def _extract_text(self, payload: dict[str, Any]) -> str:
        return _extract_chat_completions_text(payload)

    def _usage_metrics_from_payload(self, payload: dict[str, Any]) -> dict[str, int]:
        return _usage_metrics_from_chat_completions(payload)
