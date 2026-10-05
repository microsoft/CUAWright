"""OpenAI Responses API model backend."""

from __future__ import annotations

from .responses import tool_session_options

import copy
import json
from pathlib import Path
from typing import Any, Literal

from cuawright.models.tool_calls import RUN_COMMAND_TOOL, TOOL_RESPONSE_MODE, _normalize_response_items, parse_tool_call_output
from cuawright.models.base import (
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
    pending_calls: list[str] = []

    def flush_pending() -> None:
        # The Responses API rejects a function_call that is never answered, so a
        # turn interrupted before execution (format error, gate, compaction) gets
        # a synthetic output rather than poisoning every later request.
        while pending_calls:
            serialized.append(
                {
                    "type": "function_call_output",
                    "call_id": pending_calls.pop(0),
                    "output": "The command was not executed.",
                }
            )

    for message in messages:
        role = message["role"]
        if role == "exit":
            continue
        extra = message.get("extra") if isinstance(message.get("extra"), dict) else {}
        content = message.get("content", "")
        if role == "assistant":
            response_items = extra.get("response_items")
            if isinstance(response_items, list) and response_items:
                # Replay reasoning/function_call items verbatim so the hidden
                # reasoning state carries into the next turn.
                flush_pending()
                for item in response_items:
                    if not isinstance(item, dict):
                        continue
                    serialized.append(copy.deepcopy(item))
                    if item.get("type") == "function_call" and isinstance(item.get("call_id"), str):
                        pending_calls.append(item["call_id"])
                continue
            raw_response = extra.get("raw_response")
            if isinstance(raw_response, dict):
                # Keep the model's complete structured response in history. The
                # display content contains only its thought, while the command,
                # completion state, and final response live in raw_response.
                content = json.dumps(raw_response, ensure_ascii=False, separators=(",", ":"))
        if isinstance(content, str):
            serialized_content = [text_part(content)]
        else:
            serialized_content = [part for part in content if isinstance(part, dict)]

        tool_call_id = extra.get("tool_call_id")
        if role == "user" and isinstance(tool_call_id, str) and tool_call_id in pending_calls:
            pending_calls.remove(tool_call_id)
            serialized.append(
                {
                    "type": "function_call_output",
                    "call_id": tool_call_id,
                    "output": "\n".join(
                        str(part.get("text", ""))
                        for part in serialized_content
                        if part.get("type") != "input_image"
                    ),
                }
            )
            # A function_call_output carries text only; images follow as a user turn.
            image_parts = [part for part in serialized_content if part.get("type") == "input_image"]
            if image_parts:
                serialized.append(
                    {
                        "type": "message",
                        "role": "user",
                        "content": [
                            _serialize_response_content_part(part, role="user") for part in image_parts
                        ],
                    }
                )
            continue

        flush_pending()
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
    flush_pending()
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
    # json_schema is deprecated: it does not preserve encrypted reasoning across turns.
    # Keep it available for legacy configs; native tool responses are the default.
    response_mode: Literal["json_schema", "run_command_tool"] = TOOL_RESPONSE_MODE
    reasoning_effort: OptStr = ""
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
        payload = self._build_text_payload(messages)
        if self.config.response_mode == TOOL_RESPONSE_MODE:
            payload.update(tool_session_options([RUN_COMMAND_TOOL]))
            if messages and (messages[-1].get("extra") or {}).get("disable_tools"):
                payload["tool_choice"] = "none"
        else:
            payload["text"] = {"format": {"type": "json_schema", "name": "playwright_step",
                                         "schema": self._response_schema(), "strict": True}}
        return payload

    def _parse_response(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self.config.response_mode == TOOL_RESPONSE_MODE:
            return parse_tool_call_output(payload)
        return super()._parse_response(payload)

    def _response_extra(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self.config.response_mode != TOOL_RESPONSE_MODE:
            return {}
        items = _normalize_response_items(payload)
        extra = {"response_items": items}
        call = next((item for item in items if item.get("type") == "function_call"), None)
        if call:
            extra["tool_call_id"] = call["call_id"]
        extra["reasoning_summary"] = "\n".join(
            part.get("text", "") for item in items if item.get("type") == "reasoning"
            for part in item.get("summary", []) if isinstance(part, dict))
        return extra

    def _build_text_payload(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        payload = {
            "model": self.config.model_name,
            "input": _serialize_response_input(messages),
            "max_output_tokens": self.config.max_output_tokens,
        }

        if self.config.reasoning_effort:
            payload["reasoning"] = {"effort": self.config.reasoning_effort, "summary": "auto"}
        return payload

    def _request_metrics_input(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        return payload.get("input") or []

    def _extract_text(self, payload: dict[str, Any]) -> str:
        return _extract_response_text(payload)

    def _usage_metrics_from_payload(self, payload: dict[str, Any]) -> dict[str, int]:
        return _usage_metrics_from_response_payload(payload)
