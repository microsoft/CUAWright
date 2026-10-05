import copy
import hashlib
import time

from .default import CallBudget, run_loop
from ..models.responses import image_part, tool_session_options

from ..config.desktop import prompts
from ..environments.desktop.guest_tools import control
from ..exceptions import ReleaseError
from ..tools.terminal import (
    TOOLS,
    Result,
    command_arguments,
)

RETRY_DELAYS = (2, 4, 8, 16, 30, 45, 60, 90, 120)


def policy_error(exc):
    text = str(exc).lower()
    return any(
        marker in text
        for marker in (
            "content_policy_violation",
            "cyber_policy",
            "invalid_prompt",
            "usage policy",
            "cybersecurity risk",
        )
    )


def message(role, text):
    return {"role": role, "content": [{"type": "input_text", "text": text}]}


def output_text(output):
    pieces = []
    for item in output:
        if item.get("type") == "message":
            for content in item.get("content", []):
                if content.get("type") == "output_text":
                    pieces.append(content["text"])
    return "\n".join(pieces)


def normalize_output(output):
    normalized = []
    for item in output:
        kind = item.get("type")
        if kind == "reasoning":
            normalized.append(
                {
                    key: value
                    for key, value in item.items()
                    if key in ("type", "id", "summary", "encrypted_content")
                }
            )
        elif kind == "function_call":
            normalized.append(
                {
                    key: value
                    for key, value in item.items()
                    if key in ("type", "id", "call_id", "name", "arguments", "status")
                }
            )
        elif kind == "message":
            normalized.append(
                {
                    key: value
                    for key, value in item.items()
                    if key in ("type", "id", "role", "content", "status")
                }
            )
        else:
            raise ReleaseError("unsupported Responses output type")
    return normalized


class Actor:
    def __init__(self, client, guest, store, instruction, settings):
        self.client = client
        self.guest = guest
        self.store = store
        self.settings = settings
        self.base_start = [
            message("system", prompts.ACTOR),
            message("user", prompts.initial(instruction)),
        ]
        self.start = copy.deepcopy(self.base_start)
        self.context = copy.deepcopy(self.start)
        self.budget = CallBudget(settings.steps)
        self._submitted = False
        self.epoch = 0
        self.commands = 0
        self.compactions = 0
        self.midpoint_sent = False
        self.finalization_sent = False

    @property
    def used(self):
        return self.budget.used

    @used.setter
    def used(self, value):
        self.budget.used = value

    @property
    def remaining(self):
        return self.budget.remaining

    def set_phase(self, text):
        phase = message("user", text)
        self.start = copy.deepcopy(self.base_start) + [copy.deepcopy(phase)]
        self.context.append(phase)

    def drop_images(self):
        removed = 0
        context = []
        for item in self.context:
            content = item.get("content") if isinstance(item, dict) else None
            if not isinstance(content, list):
                context.append(item)
                continue
            kept = []
            for part in content:
                if isinstance(part, dict) and part.get("type") == "input_image":
                    removed += 1
                else:
                    kept.append(part)
            context.append({**item, "content": kept})
        if removed:
            context.append(
                message(
                    "user",
                    "A prior image attachment was rejected by the provider. "
                    "Continue with narrower screenshots, OCR, or metadata.",
                )
            )
            self.context = context
        return removed

    def call(self, compact=False):
        if not self.remaining:
            raise ReleaseError("model-call budget exhausted")
        request = {
            "model": self.settings.model,
            "input": copy.deepcopy(self.context),
            **tool_session_options([] if compact else TOOLS),
            "reasoning": {"effort": self.settings.reasoning, "summary": "auto"},
            "max_output_tokens": self.settings.max_output_tokens,
        }
        response = None
        policy_attempt = 0
        for attempt in range(10):
            self.store.event("request", request)
            delay = 0 if attempt == 0 else RETRY_DELAYS[attempt - 1]
            if delay:
                time.sleep(delay)
            try:
                response = self.client.responses.create(**copy.deepcopy(request))
                break
            except Exception as exc:
                payload = {
                    "attempt": attempt + 1,
                    "exception_type": type(exc).__name__,
                    "status_code": getattr(exc, "status_code", None),
                }
                if policy_error(exc):
                    policy_attempt += 1
                    payload["removed_images"] = self.drop_images()
                    self.context.append(
                        message("user", prompts.policy_recovery(policy_attempt, 10))
                    )
                    request["input"] = copy.deepcopy(self.context)
                    payload["policy_feedback"] = True
                if attempt == 9:
                    self.store.event("transport_failure", payload)
                    raise ReleaseError("OpenAI Responses transport failed") from None
                payload["next_delay_seconds"] = RETRY_DELAYS[attempt]
                self.store.event("transport_retry", payload)
        assert response is not None
        self.budget.charge()
        self.epoch += 1
        try:
            value = response.model_dump(mode="json", exclude_none=True)
        except Exception:
            raise ReleaseError("invalid Responses response") from None
        if not isinstance(value, dict) or not isinstance(value.get("output"), list):
            raise ReleaseError("invalid Responses response")
        status = value.get("status")
        if status not in ("completed", "incomplete"):
            raise ReleaseError("Responses request did not complete")
        output = normalize_output(value["output"])
        calls = [item for item in output if item.get("type") == "function_call"]
        identifiers = [item.get("call_id") for item in calls]
        if any(not isinstance(item, str) or not item for item in identifiers) or len(
            identifiers
        ) != len(set(identifiers)):
            raise ReleaseError("invalid or duplicate tool call identities")
        self.store.event(
            "response",
            {
                "step": self.used,
                "output": output,
                "status": status,
                "usage": value.get("usage"),
            },
        )
        self.context.extend(output)
        if status == "incomplete":
            details = value.get("incomplete_details") or {}
            if details.get("reason") != "max_output_tokens":
                raise ReleaseError("unsupported incomplete Responses response")
        return output, calls, status

    def add_result(self, call, result):
        text = result.text()
        self.context.append(
            {
                "type": "function_call_output",
                "call_id": call["call_id"],
                "output": text,
            }
        )
        metadata = {"call_id": call["call_id"], "output": text}
        if result.image is not None:
            metadata["image"] = {
                "sha256": hashlib.sha256(result.image).hexdigest(),
                "size_bytes": len(result.image),
                "media_type": result.media_type,
                "path": result.path,
            }
        self.store.event("command_result", metadata)
        if result.image is not None:
            self.context.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": f"Image read from VM path: {result.path}",
                        },
                        image_part(result.image, result.media_type),
                    ],
                }
            )

    def compact(self):
        self.context.append(message("user", prompts.COMPACTION))
        while self.remaining:
            output, calls, status = self.call(compact=True)
            if calls:
                raise ReleaseError("compaction returned a tool call")
            if status == "incomplete" and not calls:
                continue
            summary = output_text(output)
            if not summary.strip():
                raise ReleaseError("compaction returned no handoff")
            self.context = copy.deepcopy(self.start)
            self.context.append(message("user", prompts.resume(summary)))
            self.epoch = 0
            self.compactions += 1
            return
        raise ReleaseError("compaction exhausted shared model-call budget")

    def run(self):
        self._submitted = False
        return run_loop(self)

    def loop_ready(self):
        return bool(self.remaining)

    def loop_done(self):
        return self._submitted

    def loop_step(self):
        midpoint = not self.midpoint_sent and self.used >= round(
            self.settings.steps * 0.5
        )
        finalization = not self.finalization_sent and self.remaining <= 10
        self.context.append(
            message(
                "user",
                prompts.budget(self.used, self.settings.steps, midpoint, finalization),
            )
        )
        self.midpoint_sent |= midpoint
        self.finalization_sent |= finalization
        output, calls, status = self.call()
        if status == "incomplete":
            for call in calls:
                self.add_result(
                    call,
                    Result(
                        2,
                        stderr=(
                            "The model response ended at the output-token limit; "
                            "the incomplete command was not executed. Retry with "
                            "a shorter command."
                        ),
                    ),
                )
            return False
        if not calls:
            self.context.append(
                message(
                    "user",
                    "Your previous response contained no run_command call. "
                    "Issue the next terminal command now. If required information "
                    "is genuinely user-controlled, use "
                    "`python /opt/cuawright-tools/ask_user.py "
                    '--question "TEXT"` in a standalone run_command.',
                )
            )
        else:
            try:
                commands = [command_arguments(call) for call in calls]
                requests = [control(command) for command, _ in commands]
                if len(calls) > 1 and any(
                    request and request[0] == "submit" for request in requests
                ):
                    raise ValueError("submit must be the only command in the batch")
            except (ValueError, KeyError, TypeError):
                for call in calls:
                    self.add_result(
                        call,
                        Result(
                            2,
                            stderr="Invalid run_command batch; nothing executed. "
                            "Use exact JSON and standalone control clients.",
                        ),
                    )
            else:
                for call, (command, timeout) in zip(calls, commands):
                    result = self.guest.run(command, timeout)
                    self.commands += 1
                    self.add_result(call, result)
                    if result.completed:
                        self._submitted = True
                        return False
        return True

    def loop_maintain(self):
        if (
            self.settings.compact_every
            and self.epoch >= self.settings.compact_every
            and self.remaining > 1
        ):
            self.compact()

    def loop_result(self):
        return self.outcome("submitted" if self._submitted else "budget_exhausted")

    def outcome(self, reason):
        return {
            "stop_reason": reason,
            "model_calls": self.used,
            "commands": self.commands,
            "compactions": self.compactions,
        }
