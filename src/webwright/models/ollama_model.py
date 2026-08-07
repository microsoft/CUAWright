"""Ollama chat completions model backend.

Drop-in replacement for OpenAI/Anthropic/OpenRouter — uses local or cloud-hosted
Ollama models via the standard /v1/chat/completions endpoint.  No API key
required for local instances; set OLLAMA_API_KEY env var for cloud-hosted
Ollama endpoints that require authentication.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from webwright.models._chat_utils import (
    extract_chat_completions_text,
    metrics_input_from_chat_messages,
    serialize_chat_messages,
    usage_metrics_from_chat_completions,
)
from webwright.models.base import (
    BaseModel,
    BaseModelConfig,
    OptStr,
)

__all__ = [
    "OllamaModel",
    "OllamaModelConfig",
]


class OllamaModelConfig(BaseModelConfig):
    model_name: OptStr = "llama3.2"
    ollama_api_key: OptStr = ""
    ollama_endpoint: OptStr = "http://localhost:11434/v1/chat/completions"
    ollama_extra_body: dict[str, Any] = {}


def _is_localhost(endpoint: str) -> bool:
    host = (urlparse(endpoint).hostname or "").lower()
    return host in ("localhost", "127.0.0.1", "::1")


class OllamaModel(BaseModel):
    _API_KEY_FIELD = "ollama_api_key"
    _ENV_VAR = "OLLAMA_API_KEY"
    _LOG_SOURCE = "ollama"
    _MAX_RATE_LIMIT_RETRIES = 5
    _MAX_TRANSIENT_RETRIES = 5
    _DEFAULT_CONFIG_CLASS = OllamaModelConfig

    def __init__(self, *, config_class: type | None = None, **kwargs):
        # Localhost Ollama doesn't need an API key — skip the key check.
        endpoint = kwargs.get("ollama_endpoint", "")
        if endpoint and _is_localhost(endpoint):
            if "ollama_api_key" not in kwargs:
                kwargs["ollama_api_key"] = "sk-noop"
        super().__init__(config_class=config_class, **kwargs)

    def _request_headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.config.ollama_api_key and self.config.ollama_api_key != "sk-noop":
            headers["Authorization"] = f"Bearer {self.config.ollama_api_key}"
        return headers

    def _post_url(self) -> str:
        return self.config.ollama_endpoint

    def _build_payload(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.config.model_name,
            "messages": serialize_chat_messages(messages),
            "stream": False,
            "response_format": {"type": "json_object"},
            "max_tokens": self.config.max_output_tokens,
        }
        if self.config.ollama_extra_body:
            payload.update(self.config.ollama_extra_body)
        return payload

    def _build_text_payload(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.config.model_name,
            "messages": serialize_chat_messages(messages),
            "stream": False,
            "max_tokens": self.config.max_output_tokens,
        }
        if self.config.ollama_extra_body:
            payload.update(self.config.ollama_extra_body)
        return payload

    def _request_metrics_input(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        return metrics_input_from_chat_messages(payload.get("messages") or [])

    def _extract_text(self, payload: dict[str, Any]) -> str:
        return extract_chat_completions_text(payload)

    def _usage_metrics_from_payload(self, payload: dict[str, Any]) -> dict[str, int]:
        return usage_metrics_from_chat_completions(payload)
