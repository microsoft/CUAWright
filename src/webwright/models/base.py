from __future__ import annotations

import asyncio
import base64
import json
import mimetypes
import os
import subprocess
from pathlib import Path
from typing import Annotated, Any

import httpx
from jinja2 import StrictUndefined, Template
from pydantic import BaseModel as PydanticBaseModel, BeforeValidator, field_validator

from webwright.exceptions import FormatError
from webwright.utils.logging import append_runtime_log
from webwright.utils.runtime import run_async


def _none_to_str(value: Any) -> str:
    return "" if value is None else str(value)


# String field that coerces None -> "" and any value -> str via pydantic.
OptStr = Annotated[str, BeforeValidator(_none_to_str)]

MAX_JSON_PARSE_RETRIES = 3
DEFAULT_OBSERVATION_TEMPLATE = """Observation:
Status: {{ 'ok' if observation.success else 'error' }}
URL: {{ observation.url }}
Title: {{ observation.title }}
{% if observation.exception %}Exception:
{{ observation.exception }}
{% endif %}{% if observation.console_output %}Console output:
{{ observation.console_output }}
{% endif %}{% if observation.aria_snapshot %}ARIA snapshot:
{{ observation.aria_snapshot }}
{% endif %}{% if observation.screenshot_path %}Screenshot path: {{ observation.screenshot_path }}
{% endif %}"""
DEFAULT_FORMAT_ERROR_TEMPLATE = """Format error:

{{ error }}

Please respond with strict JSON using exactly these fields:
- thought: short reasoning about the next step
- bash_command: exactly one shell command for local-workspace tasks
- python_code: exactly one async Python browser step for local-browser tasks
- done: boolean indicating whether the task is complete
- final_response: final natural-language answer when done, otherwise empty
"""

ACTION_FIELDS = {"bash_command", "python_code"}


def _is_rate_limit_error(exc: BaseException | None) -> bool:
    current: BaseException | None = exc
    while current is not None:
        status_code = getattr(current, "status_code", None)
        if status_code == 429:
            return True
        response = getattr(current, "response", None)
        if getattr(response, "status_code", None) == 429:
            return True
        text = str(current).lower()
        if "rate limit" in text or "ratelimit" in text or "too many requests" in text:
            return True
        current = current.__cause__ if isinstance(current.__cause__, BaseException) else None
    return False


def _is_transient_http_error(exc: BaseException | None) -> bool:
    """True for retryable transient HTTP failures (timeouts, 5xx, conn resets, ...).

    Applies to any HTTP backend, not just gateway/proxy setups.
    """
    current: BaseException | None = exc
    while current is not None:
        if isinstance(current, (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError)):
            return True
        status_code = getattr(current, "status_code", None)
        if isinstance(status_code, int) and status_code in {408, 409, 425, 500, 502, 503, 504}:
            return True
        response = getattr(current, "response", None)
        response_status = getattr(response, "status_code", None)
        if isinstance(response_status, int) and response_status in {408, 409, 425, 500, 502, 503, 504}:
            return True
        text = str(current).lower()
        if any(
            needle in text
            for needle in (
                "bad gateway",
                "gateway timeout",
                "server disconnected",
                "temporary failure",
                "temporarily unavailable",
                "connection reset",
                "connection aborted",
                "timed out",
            )
        ):
            return True
        current = current.__cause__ if isinstance(current.__cause__, BaseException) else None
    return False


def parse_json_output(raw: str, *, action_field: str = "bash_command") -> dict[str, Any]:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Unable to parse JSON output: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError("Model output was JSON but not a JSON object.")
    # Strict-schema responses cannot have done=true with a non-empty action;
    # tolerate it from non-strict callers by demoting `done`.
    action_text = str(parsed.get(action_field, "") or "").strip()
    if action_text and bool(parsed.get("done", False)):
        parsed = dict(parsed)
        parsed["done"] = False
    return parsed


def _validate_bash_command(command: str) -> None:
    result = subprocess.run(
        ["/bin/bash", "-n"],
        input=command,
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode == 0:
        return
    error = (result.stderr or result.stdout or "bash syntax check failed").strip()
    raise ValueError(f"Invalid bash_command syntax: {error}")


def text_part(text: str) -> dict[str, Any]:
    return {"type": "input_text", "text": text}


def image_part_from_path(path: Path) -> dict[str, Any]:
    mime_type, _ = mimetypes.guess_type(str(path))
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return {
        "type": "input_image",
        "image_url": f"data:{mime_type or 'image/png'};base64,{encoded}",
        "detail": "high",
    }


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _request_metrics_from_serialized_input(serialized_input: list[dict[str, Any]]) -> dict[str, int]:
    message_count = len(serialized_input)
    text_part_count = 0
    image_part_count = 0
    text_chars = 0

    for item in serialized_input:
        for content in item.get("content") or []:
            if not isinstance(content, dict):
                continue
            part_type = content.get("type")
            if part_type in {"input_text", "output_text"}:
                text_part_count += 1
                text_chars += len(str(content.get("text", "") or ""))
            elif part_type == "input_image":
                image_part_count += 1

    serialized_chars = len(json.dumps(serialized_input, ensure_ascii=False))

    return {
        "message_count": message_count,
        "text_part_count": text_part_count,
        "image_part_count": image_part_count,
        "text_chars": text_chars,
        "serialized_chars": serialized_chars,
    }


_REQUEST_METRIC_KEYS = (
    "message_count",
    "text_part_count",
    "image_part_count",
    "text_chars",
    "serialized_chars",
)
_USAGE_METRIC_KEYS = (
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "cached_input_tokens",
    "reasoning_output_tokens",
)


class BaseModelConfig(PydanticBaseModel):
    """Fields common to every model backend (OpenAI, Anthropic, ...)."""

    model_name: OptStr = ""
    max_output_tokens: int = 4000
    request_timeout_seconds: int = 120
    error_log_path: Path | None = None
    observation_template: OptStr = DEFAULT_OBSERVATION_TEMPLATE
    format_error_template: OptStr = DEFAULT_FORMAT_ERROR_TEMPLATE
    attach_observation_screenshot: bool = True
    action_field: str = "bash_command"

    @field_validator("action_field")
    @classmethod
    def validate_action_field(cls, value: str) -> str:
        normalized = value.strip()
        if normalized not in ACTION_FIELDS:
            raise ValueError(f"action_field must be one of: {', '.join(sorted(ACTION_FIELDS))}")
        return normalized


class BaseModel:
    """Provider-agnostic model backend.

    Subclasses must override:
      - class constants ``_API_KEY_FIELD``, ``_ENV_VAR``, ``_LOG_SOURCE``,
        ``_DEFAULT_CONFIG_CLASS`` (and optionally ``_MAX_RATE_LIMIT_RETRIES``,
        ``_MAX_TRANSIENT_RETRIES``)
      - ``_request_headers``, ``_post_url``
      - ``_build_payload``, ``_request_metrics_input``
      - ``_extract_text``, ``_usage_metrics_from_payload``

    Optionally override ``_rate_limit_backoff`` / ``_transient_backoff`` for
    custom retry timing.
    """

    _API_KEY_FIELD: str = ""
    _ENV_VAR: str = ""
    _LOG_SOURCE: str = ""
    _MAX_RATE_LIMIT_RETRIES: int = 5
    _MAX_TRANSIENT_RETRIES: int = 5
    _DEFAULT_CONFIG_CLASS: type = BaseModelConfig

    def __init__(self, *, config_class: type | None = None, **kwargs):
        self.config = (config_class or self._DEFAULT_CONFIG_CLASS)(**kwargs)
        self._last_request_metrics: dict[str, int] = {k: 0 for k in _REQUEST_METRIC_KEYS}
        self._last_usage_metrics: dict[str, int] = {k: 0 for k in _USAGE_METRIC_KEYS}
        self._cumulative_request_metrics: dict[str, int] = dict(self._last_request_metrics)
        self._cumulative_usage_metrics: dict[str, int] = dict(self._last_usage_metrics)

        if self._API_KEY_FIELD:
            if not getattr(self.config, self._API_KEY_FIELD, ""):
                setattr(self.config, self._API_KEY_FIELD, os.environ.get(self._ENV_VAR, ""))
            if not getattr(self.config, self._API_KEY_FIELD, ""):
                raise RuntimeError(f"Missing {self._ENV_VAR}.")

    # ---- subclass extension points ------------------------------------------------

    def _request_headers(self) -> dict[str, str]:
        raise NotImplementedError

    def _post_url(self) -> str:
        raise NotImplementedError

    def _build_payload(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        raise NotImplementedError

    def _build_text_payload(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        return self._build_payload(messages)

    def _request_metrics_input(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        raise NotImplementedError

    def _extract_text(self, payload: dict[str, Any]) -> str:
        raise NotImplementedError

    def _usage_metrics_from_payload(self, payload: dict[str, Any]) -> dict[str, int]:
        raise NotImplementedError

    async def _rate_limit_backoff(self, attempt: int, exc: BaseException) -> None:
        await asyncio.sleep(min(5 * (attempt + 1), 30))

    async def _transient_backoff(self, attempt: int, exc: BaseException) -> None:
        await asyncio.sleep(min(2 * (attempt + 1), 10))

    # ---- shared infrastructure ----------------------------------------------------

    def get_template_vars(self, **kwargs) -> dict[str, Any]:
        vars: dict[str, Any] = {
            "action_field": self.config.action_field,
            "model_name": self.config.model_name,
        }
        for k, v in self._last_request_metrics.items():
            vars[f"last_request_{k}"] = v
        for k, v in self._last_usage_metrics.items():
            vars[f"last_request_{k}"] = v
        for k, v in self._cumulative_request_metrics.items():
            vars[f"cumulative_request_{k}"] = v
        for k, v in self._cumulative_usage_metrics.items():
            vars[f"cumulative_{k}"] = v
        vars.update(kwargs)
        return vars

    def _response_schema(self) -> dict[str, Any]:
        action_field = self.config.action_field
        return {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "thought": {"type": "string"},
                action_field: {"type": "string"},
                "done": {"type": "boolean"},
                "final_response": {"type": "string"},
            },
            "required": ["thought", action_field, "done", "final_response"],
        }

    def _usage_snapshot(self) -> dict[str, dict[str, int]]:
        return {
            "last_request": {
                "message_count": self._last_request_metrics["message_count"],
                "text_part_count": self._last_request_metrics["text_part_count"],
                "image_part_count": self._last_request_metrics["image_part_count"],
                "input_tokens": self._last_usage_metrics["input_tokens"],
                "cached_input_tokens": self._last_usage_metrics["cached_input_tokens"],
            },
            "last_response": dict(self._last_usage_metrics),
            "cumulative_request": {
                "message_count": self._cumulative_request_metrics["message_count"],
                "text_part_count": self._cumulative_request_metrics["text_part_count"],
                "image_part_count": self._cumulative_request_metrics["image_part_count"],
                "input_tokens": self._cumulative_usage_metrics["input_tokens"],
                "cached_input_tokens": self._cumulative_usage_metrics["cached_input_tokens"],
            },
            "cumulative_response": dict(self._cumulative_usage_metrics),
        }

    def _log_gateway_error(self, *, event: str, attempt: int, error: BaseException) -> None:
        response = getattr(error, "response", None)
        response_text = ""
        if response is not None:
            try:
                response_text = str(getattr(response, "text", "") or "")
            except Exception:
                response_text = ""
        if len(response_text) > 4000:
            response_text = response_text[:4000]

        append_runtime_log(
            self.config.error_log_path,
            source=self._LOG_SOURCE,
            event=event,
            model_name=self.config.model_name,
            endpoint=self._post_url(),
            attempt=attempt,
            error_type=type(error).__name__,
            error=str(error),
            status_code=getattr(response, "status_code", None),
            response_text=response_text,
        )

    def _raw_response_log_path(self) -> Path | None:
        if self.config.error_log_path is None:
            return None
        return self.config.error_log_path.parent / "raw_responses.jsonl"

    def format_message(self, **kwargs) -> dict[str, Any]:
        role = kwargs["role"]
        content = kwargs.get("content", "")
        extra = kwargs.get("extra", {})
        return {"role": role, "content": content, "extra": extra}

    def format_observation_messages(
        self,
        message: dict[str, Any],
        outputs: list[dict[str, Any]],
        template_vars: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        observation_messages: list[dict[str, Any]] = []
        for output in outputs:
            observation = output.get("observation", {})
            content = Template(self.config.observation_template, undefined=StrictUndefined).render(
                output=output,
                observation=observation,
                **(template_vars or {}),
            )

            parts: list[dict[str, Any]] = [text_part(content)]
            screenshot_path = observation.get("screenshot_path")
            if self.config.attach_observation_screenshot and screenshot_path:
                parts.append(image_part_from_path(Path(screenshot_path)))

            observation_messages.append(
                self.format_message(role="user", content=parts, extra={"observation": observation})
            )
        return observation_messages

    def _format_error(self, *, raw_text: str, error: str) -> FormatError:
        return FormatError(
            self.format_message(
                role="user",
                content=Template(self.config.format_error_template, undefined=StrictUndefined).render(
                    error=error,
                    model_response=raw_text,
                    **self.get_template_vars(),
                ),
                extra={
                    "interrupt_type": "FormatError",
                    "model_response": raw_text,
                },
            )
        )

    def _format_repair_message(self, *, raw_text: str, error: str) -> dict[str, Any]:
        return self.format_message(
            role="user",
            content=Template(self.config.format_error_template, undefined=StrictUndefined).render(
                error=error,
                model_response=raw_text,
                **self.get_template_vars(),
            ),
            extra={
                "interrupt_type": "FormatErrorRetry",
                "model_response": raw_text,
            },
        )

    async def _post_with_retries(self, payload: dict[str, Any]) -> dict[str, Any]:
        headers = self._request_headers()
        url = self._post_url()
        for attempt in range(max(self._MAX_RATE_LIMIT_RETRIES, self._MAX_TRANSIENT_RETRIES) + 1):
            try:
                async with httpx.AsyncClient(timeout=self.config.request_timeout_seconds) as client:
                    response = await client.post(url, headers=headers, json=payload)
                    response.raise_for_status()
                    return response.json()
            except Exception as exc:
                if _is_rate_limit_error(exc):
                    self._log_gateway_error(
                        event="rate_limit_error", attempt=attempt + 1, error=exc
                    )
                    if attempt >= self._MAX_RATE_LIMIT_RETRIES:
                        raise
                    await self._rate_limit_backoff(attempt, exc)
                    continue
                if _is_transient_http_error(exc):
                    self._log_gateway_error(
                        event="transient_http_error", attempt=attempt + 1, error=exc
                    )
                    if attempt >= self._MAX_TRANSIENT_RETRIES:
                        raise
                    await self._transient_backoff(attempt, exc)
                    continue
                self._log_gateway_error(
                    event="fatal_gateway_error", attempt=attempt + 1, error=exc
                )
                raise
        raise RuntimeError("Exceeded retry budget without exception or success.")

    async def _query_async(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        last_error: ValueError | None = None
        raw_text = ""
        request_messages = list(messages)
        for attempt_index in range(MAX_JSON_PARSE_RETRIES + 1):
            payload = self._build_payload(request_messages)
            request_metrics = _request_metrics_from_serialized_input(self._request_metrics_input(payload))
            self._last_request_metrics = dict(request_metrics)
            for key, value in request_metrics.items():
                self._cumulative_request_metrics[key] += value

            response_payload = await self._post_with_retries(payload)

            usage_metrics = self._usage_metrics_from_payload(response_payload)
            self._last_usage_metrics = dict(usage_metrics)
            for key, value in usage_metrics.items():
                self._cumulative_usage_metrics[key] += value

            raw_text = self._extract_text(response_payload)
            append_runtime_log(
                self._raw_response_log_path(),
                source="model",
                event="raw_text",
                attempt=attempt_index + 1,
                raw_text=raw_text,
            )
            try:
                parsed = parse_json_output(raw_text, action_field=self.config.action_field)
                break
            except ValueError as exc:
                last_error = exc
                if attempt_index < MAX_JSON_PARSE_RETRIES:
                    request_messages.append(
                        self._format_repair_message(raw_text=raw_text, error=str(exc))
                    )
        else:
            raise self._format_error(
                raw_text=raw_text,
                error=str(last_error or ValueError("Unable to parse model output.")),
            )

        actions: list[dict[str, Any]] = []
        action_field = self.config.action_field
        action_text = str(parsed.get(action_field, "") or "").strip()
        if action_text:
            action = {action_field: action_text, "command": action_text}
            if action_field == "bash_command":
                action["bash_command"] = action_text
                try:
                    _validate_bash_command(action_text)
                except ValueError as exc:
                    raise self._format_error(raw_text=raw_text, error=str(exc))
            else:
                action["python_code"] = action_text
            actions.append(action)

        return self.format_message(
            role="assistant",
            content=parsed.get("thought", ""),
            extra={
                "actions": actions,
                "done": bool(parsed.get("done", False)),
                "final_response": parsed.get("final_response", ""),
                "raw_response": parsed,
                "usage": self._usage_snapshot(),
            },
        )

    async def _complete_text_async(
        self,
        messages: list[dict[str, Any]],
        *,
        max_output_tokens: int | None = None,
    ) -> str:
        original_max_output_tokens = self.config.max_output_tokens
        if max_output_tokens is not None:
            self.config.max_output_tokens = max_output_tokens
        try:
            payload = self._build_text_payload(messages)
            request_metrics = _request_metrics_from_serialized_input(self._request_metrics_input(payload))
            self._last_request_metrics = dict(request_metrics)
            for key, value in request_metrics.items():
                self._cumulative_request_metrics[key] += value

            response_payload = await self._post_with_retries(payload)

            usage_metrics = self._usage_metrics_from_payload(response_payload)
            self._last_usage_metrics = dict(usage_metrics)
            for key, value in usage_metrics.items():
                self._cumulative_usage_metrics[key] += value

            raw_text = self._extract_text(response_payload)
            append_runtime_log(
                self._raw_response_log_path(),
                source="model",
                event="raw_text",
                raw_text=raw_text,
            )
            return raw_text
        finally:
            self.config.max_output_tokens = original_max_output_tokens

    def __call__(
        self,
        messages: list[dict[str, Any]],
        **kwargs: Any,
    ) -> str:
        return run_async(self._complete_text_async(messages, **kwargs))

    def query(self, messages: list[dict[str, Any]], **kwargs) -> dict[str, Any]:
        return run_async(self._query_async(messages))

    def serialize(self) -> dict[str, Any]:
        config_dump = self.config.model_dump(mode="json")
        if self._API_KEY_FIELD:
            config_dump[self._API_KEY_FIELD] = "<redacted>"
        return {
            "model": {
                "config": config_dump,
                "usage": {
                    **self._usage_snapshot(),
                },
                "model_type": f"{self.__class__.__module__}.{self.__class__.__name__}",
            }
        }
