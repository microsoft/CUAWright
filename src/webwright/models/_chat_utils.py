"""Shared chat-completions serialization helpers used by Ollama and OpenRouter model backends."""

from __future__ import annotations

from typing import Any

from webwright.models.base import _safe_int


def serialize_chat_content_part(part: dict[str, Any]) -> dict[str, Any] | None:
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


def serialize_chat_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
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
            for serialized_part in [serialize_chat_content_part(part)]
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


def metrics_input_from_chat_messages(chat_messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
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


def extract_chat_completions_text(payload: dict[str, Any]) -> str:
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


def usage_metrics_from_chat_completions(payload: dict[str, Any]) -> dict[str, int]:
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
