"""Google Gemini (AI Studio) model backend."""

from __future__ import annotations

import copy
from typing import Any

from webwright.models.base import (
    BaseModel,
    BaseModelConfig,
    OptStr,
    _safe_int,
)


def _strip_additional_properties(schema: dict[str, Any]) -> dict[str, Any]:
    # Gemini responseSchema does not support additionalProperties — strip recursively.
    result = {k: v for k, v in schema.items() if k != "additionalProperties"}
    if "properties" in result and isinstance(result["properties"], dict):
        result["properties"] = {
            k: _strip_additional_properties(v) if isinstance(v, dict) else v
            for k, v in result["properties"].items()
        }
    return result


def _serialize_gemini_messages(
    messages: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    system_chunks: list[str] = []
    contents: list[dict[str, Any]] = []

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

        gemini_role = "model" if role == "assistant" else "user"

        if isinstance(content, str):
            parts: list[dict[str, Any]] = [{"text": content}]
        else:
            parts = []
            for part in content:
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "input_image":
                    image_url = part.get("image_url", "")
                    if image_url.startswith("data:"):
                        header, _, encoded = image_url.partition(",")
                        mime_type = header.split(";")[0].removeprefix("data:") or "image/png"
                        parts.append({"inlineData": {"mimeType": mime_type, "data": encoded}})
                else:
                    parts.append({"text": part.get("text", "")})

        if parts:
            contents.append({"role": gemini_role, "parts": parts})

    system_instruction = (
        {"parts": [{"text": "\n\n".join(system_chunks)}]} if system_chunks else None
    )
    return system_instruction, contents


def _extract_gemini_text(payload: dict[str, Any]) -> str:
    candidates = payload.get("candidates") or []
    if not candidates or not isinstance(candidates[0], dict):
        return ""
    content = candidates[0].get("content", {})
    parts = content.get("parts") or [] if isinstance(content, dict) else []
    return "\n".join(p.get("text", "") for p in parts if isinstance(p, dict) and "text" in p)


def _usage_from_gemini_payload(payload: dict[str, Any]) -> dict[str, int]:
    meta = payload.get("usageMetadata") or {}
    input_tokens = _safe_int(meta.get("promptTokenCount"))
    output_tokens = _safe_int(meta.get("candidatesTokenCount"))
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": _safe_int(meta.get("totalTokenCount")) or input_tokens + output_tokens,
        "cached_input_tokens": _safe_int(meta.get("cachedContentTokenCount")),
        "reasoning_output_tokens": 0,
    }


def _metrics_input_from_gemini(
    system_instruction: dict[str, Any] | None,
    contents: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    if system_instruction:
        for part in system_instruction.get("parts") or []:
            items.append({"content": [{"type": "input_text", "text": part.get("text", "")}]})
    for msg in contents:
        normalized: list[dict[str, Any]] = []
        for part in msg.get("parts") or []:
            if "text" in part:
                normalized.append({"type": "input_text", "text": part["text"]})
            elif "inlineData" in part:
                normalized.append({"type": "input_image"})
        items.append({"content": normalized})
    return items


class GeminiModelConfig(BaseModelConfig):
    model_name: OptStr = "gemini-3.1-pro-preview"
    gemini_api_key: OptStr = ""
    gemini_endpoint_base: OptStr = "https://generativelanguage.googleapis.com/v1beta/models"


class GeminiModel(BaseModel):
    _API_KEY_FIELD = "gemini_api_key"
    _ENV_VAR = "GEMINI_API_KEY"
    _LOG_SOURCE = "gemini"
    _DEFAULT_CONFIG_CLASS = GeminiModelConfig

    def _request_headers(self) -> dict[str, str]:
        return {"Content-Type": "application/json"}

    def _post_url(self) -> str:
        return (
            f"{self.config.gemini_endpoint_base}"
            f"/{self.config.model_name}:generateContent"
            f"?key={self.config.gemini_api_key}"
        )

    def _build_payload(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        system_instruction, contents = _serialize_gemini_messages(messages)
        schema = _strip_additional_properties(copy.deepcopy(self._response_schema()))
        payload: dict[str, Any] = {
            "contents": contents,
            "generationConfig": {
                "maxOutputTokens": self.config.max_output_tokens,
                "responseMimeType": "application/json",
                "responseSchema": schema,
            },
        }
        if system_instruction:
            payload["systemInstruction"] = system_instruction
        return payload

    def _build_text_payload(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        system_instruction, contents = _serialize_gemini_messages(messages)
        payload: dict[str, Any] = {
            "contents": contents,
            "generationConfig": {"maxOutputTokens": self.config.max_output_tokens},
        }
        if system_instruction:
            payload["systemInstruction"] = system_instruction
        return payload

    def _request_metrics_input(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        return _metrics_input_from_gemini(
            payload.get("systemInstruction"),
            payload.get("contents") or [],
        )

    def _extract_text(self, payload: dict[str, Any]) -> str:
        return _extract_gemini_text(payload)

    def _usage_metrics_from_payload(self, payload: dict[str, Any]) -> dict[str, int]:
        return _usage_from_gemini_payload(payload)
