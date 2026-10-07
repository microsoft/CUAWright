"""Google Gemini API model backend."""

from __future__ import annotations

import json
from typing import Any

from webwright.models.base import (
    BaseModel,
    BaseModelConfig,
    OptStr,
    _safe_int,
    text_part,
)

__all__ = [
    "GeminiModel",
    "GeminiModelConfig",
]


def _serialize_gemini_content(content: Any) -> list[dict[str, Any]]:
    if isinstance(content, str):
        return [{"text": content}]

    parts = []
    for part in content:
        if not isinstance(part, dict):
            continue

        if part.get("type") in ["text", "input_text", "output_text"]:
            parts.append({"text": part.get("text", "")})
        elif part.get("type") == "image":
            image_data = part.get("source", {})
            if image_data.get("type") == "base64":
                parts.append({
                    "inlineData": {
                        "mimeType": image_data.get("media_type", "image/jpeg"),
                        "data": image_data.get("data", "")
                    }
                })
        elif part.get("type") == "input_image":
            image_url = part.get("image_url", "")
            if image_url.startswith("data:"):
                try:
                    header, data = image_url.split(",", 1)
                    mime_type = header.split(";")[0].split(":")[1]
                    parts.append({
                        "inlineData": {
                            "mimeType": mime_type,
                            "data": data
                        }
                    })
                except Exception:
                    pass

    return parts if parts else [{"text": ""}]


def _serialize_gemini_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    serialized = []
    system_content = []

    for message in messages:
        role = message.get("role", "user")
        content = message.get("content", "")

        if role == "exit":
            continue
        elif role == "system":
            if isinstance(content, str):
                system_content.append(content)
            continue

        gemini_role = "model" if role == "assistant" else "user"

        # If this is the first user message and we have system content, prepend it
        if gemini_role == "user" and system_content and not serialized:
            system_text = "\n".join(system_content)
            if isinstance(content, str):
                content = f"{system_text}\n\n{content}"
            else:
                # Prepend system text as first part
                content = [text_part(system_text)] + content
            system_content = []

        serialized.append({
            "role": gemini_role,
            "parts": _serialize_gemini_content(content)
        })

    return serialized


def _extract_gemini_response_text(payload: dict[str, Any]) -> str:
    candidates = payload.get("candidates", [])
    if not candidates:
        return ""

    content = candidates[0].get("content", {})
    parts = content.get("parts", [])

    texts = []
    for part in parts:
        if isinstance(part, dict) and "text" in part:
            texts.append(part["text"])

    return "\n".join(texts)


def _usage_metrics_from_gemini_response(payload: dict[str, Any]) -> dict[str, int]:
    usage = payload.get("usageMetadata", {})

    return {
        "input_tokens": _safe_int(usage.get("promptTokenCount")),
        "output_tokens": _safe_int(usage.get("candidatesTokenCount")),
        "total_tokens": _safe_int(usage.get("totalTokenCount")),
        "cached_input_tokens": _safe_int(usage.get("cachedContentTokenCount", 0)),
        "reasoning_output_tokens": 0,
    }


def _convert_schema_to_gemini_format(schema: dict[str, Any]) -> dict[str, Any]:
    if not schema:
        return {}

    def convert_type(json_type: str) -> str:
        """Convert JSON schema types to Gemini types."""
        type_mapping = {
            "string": "STRING",
            "number": "NUMBER",
            "integer": "INTEGER",
            "boolean": "BOOLEAN",
            "array": "ARRAY",
            "object": "OBJECT",
        }
        return type_mapping.get(json_type, json_type.upper())

    def convert_schema_recursive(obj: dict[str, Any]) -> dict[str, Any]:
        result = {}

        if "type" in obj:
            result["type"] = convert_type(obj["type"])

        if "properties" in obj:
            result["properties"] = {
                key: convert_schema_recursive(value)
                for key, value in obj.get("properties", {}).items()
            }

        if "items" in obj:
            result["items"] = convert_schema_recursive(obj["items"])

        if "required" in obj:
            result["required"] = obj["required"]

        if "enum" in obj:
            result["enum"] = obj["enum"]

        return result

    return convert_schema_recursive(schema)


class GeminiModelConfig(BaseModelConfig):
    model_name: OptStr = "gemini-3.5-flash"
    gemini_api_key: OptStr = ""
    gemini_endpoint: OptStr = "https://generativelanguage.googleapis.com/v1beta"


class GeminiModel(BaseModel):
    _API_KEY_FIELD = "gemini_api_key"
    _ENV_VAR = "GEMINI_API_KEY"
    _LOG_SOURCE = "gemini"
    _MAX_RATE_LIMIT_RETRIES = 5
    _MAX_TRANSIENT_RETRIES = 5
    _DEFAULT_CONFIG_CLASS = GeminiModelConfig

    def _request_headers(self) -> dict[str, str]:
        # Use header authentication as recommended by Google
        return {
            "Content-Type": "application/json",
            "x-goog-api-key": self.config.gemini_api_key,
        }

    def _post_url(self) -> str:
        base_url = self.config.gemini_endpoint.rstrip("/")
        model_name = self.config.model_name
        return f"{base_url}/models/{model_name}:generateContent"

    def _build_payload(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "contents": _serialize_gemini_messages(messages),
            "generationConfig": {
                "maxOutputTokens": self.config.max_output_tokens,
                "temperature": 0.0,
            },
        }

    def _build_text_payload(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        """Build payload for text response."""
        return {
            "contents": _serialize_gemini_messages(messages),
            "generationConfig": {
                "maxOutputTokens": self.config.max_output_tokens,
                "temperature": 0.0,
            },
        }

    def _request_metrics_input(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        return payload.get("contents") or []

    def _extract_text(self, payload: dict[str, Any]) -> str:
        """Extract text from Gemini response, handling JSON responses."""
        text = _extract_gemini_response_text(payload)

        # If the response looks like JSON, try to parse it and extract relevant text
        if text.strip().startswith("{"):
            try:
                json_data = json.loads(text)
                # Look for common text fields
                for field in ["text", "output", "result", "response", "output_text"]:
                    if field in json_data:
                        return str(json_data[field])
                # If no specific field found, return the JSON as string
                return text
            except json.JSONDecodeError:
                pass

        return text

    def _usage_metrics_from_payload(self, payload: dict[str, Any]) -> dict[str, int]:
        return _usage_metrics_from_gemini_response(payload)

    def _is_rate_limit_error(self, response: dict[str, Any]) -> bool:
        error = response.get("error", {})
        code = error.get("code")
        status = error.get("status", "")
        # Gemini uses HTTP status codes: 429 for rate limit
        return code == 429 or status in ["RESOURCE_EXHAUSTED", "RATE_LIMIT_EXCEEDED"]

    def _is_transient_error(self, response: dict[str, Any]) -> bool:
        """Check if the response indicates a transient error."""
        error = response.get("error", {})
        code = error.get("code")
        status = error.get("status", "")
        # 500: Internal error, 503: Service unavailable
        return code in [500, 503] or status in ["UNAVAILABLE", "DEADLINE_EXCEEDED", "INTERNAL"]