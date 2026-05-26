"""OpenAI Responses API model backend."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from webwright.models.base import (
    BaseModel,
    BaseModelConfig,
    OptStr,
    _safe_int,
    image_part_from_path,
    text_part,
)

__all__ = [
    "OpenAIModel",
    "OpenAIModelConfig",
    "_extract_response_text",
    "text_part",
]


def _serialize_response_content_part(part: dict[str, Any], *, role: str) -> dict[str, Any]:
    if part.get("type") == "input_image":
        return {
            "type": "input_image",
            "image_url": part.get("image_url", ""),
            "detail": part.get("detail", "high"),
        }
    text = part.get("text", "")
    if role == "assistant":
        return {"type": "output_text", "text": text}
    return {"type": "input_text", "text": text}


def _serialize_response_input(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    serialized: list[dict[str, Any]] = []
    for message in messages:
        role = message["role"]
        if role == "exit":
            continue
        content = message.get("content", "")
        if isinstance(content, str):
            serialized_content = [text_part(content)]
        else:
            serialized_content = [part for part in content if isinstance(part, dict)]
        # The Responses API accepts "developer" for system-style instructions.
        mapped_role = "developer" if role == "system" else role
        serialized.append(
            {
                "type": "message",
                "role": mapped_role,
                "content": [
                    _serialize_response_content_part(part, role=mapped_role)
                    for part in serialized_content
                ],
            }
        )
    return serialized


def _extract_response_text(payload: dict[str, Any]) -> str:
    output_text = payload.get("output_text")
    if isinstance(output_text, str) and output_text:
        return output_text

    texts: list[str] = []
    for item in payload.get("output") or []:
        if not isinstance(item, dict):
            continue
        if item.get("type") != "message":
            continue
        for content in item.get("content") or []:
            if not isinstance(content, dict):
                continue
            if isinstance(content.get("text"), str):
                texts.append(content["text"])
            elif isinstance(content.get("output_text"), str):
                texts.append(content["output_text"])
    return "\n".join(texts)


def _usage_metrics_from_response_payload(payload: dict[str, Any]) -> dict[str, int]:
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        usage = {}
    input_details = usage.get("input_tokens_details")
    if not isinstance(input_details, dict):
        input_details = {}
    output_details = usage.get("output_tokens_details")
    if not isinstance(output_details, dict):
        output_details = {}

    return {
        "input_tokens": _safe_int(usage.get("input_tokens")),
        "output_tokens": _safe_int(usage.get("output_tokens")),
        "total_tokens": _safe_int(usage.get("total_tokens")),
        "cached_input_tokens": _safe_int(input_details.get("cached_tokens")),
        "reasoning_output_tokens": _safe_int(output_details.get("reasoning_tokens")),
    }


class OpenAIModelConfig(BaseModelConfig):
    model_name: OptStr = "gpt-4o"
    openai_api_key: OptStr = ""
    openai_endpoint: OptStr = "https://api.openai.com/v1/responses"


class OpenAIModel(BaseModel):
    _API_KEY_FIELD = "openai_api_key"
    _ENV_VAR = "OPENAI_API_KEY"
    _LOG_SOURCE = "openai"
    _MAX_RATE_LIMIT_RETRIES = 5
    _MAX_TRANSIENT_RETRIES = 5
    _DEFAULT_CONFIG_CLASS = OpenAIModelConfig

    def _request_headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.config.openai_api_key}",
        }

    def _post_url(self) -> str:
        return self.config.openai_endpoint

    def _build_payload(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "model": self.config.model_name,
            "input": _serialize_response_input(messages),
            "max_output_tokens": self.config.max_output_tokens,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "playwright_step",
                    "schema": self._response_schema(),
                    "strict": True,
                }
            },
        }

    def _build_text_payload(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "model": self.config.model_name,
            "input": _serialize_response_input(messages),
            "max_output_tokens": self.config.max_output_tokens,
        }

    def _request_metrics_input(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        return payload.get("input") or []

    def _extract_text(self, payload: dict[str, Any]) -> str:
        return _extract_response_text(payload)

    def _usage_metrics_from_payload(self, payload: dict[str, Any]) -> dict[str, int]:
        return _usage_metrics_from_response_payload(payload)
