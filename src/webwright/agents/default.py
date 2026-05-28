from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from jinja2 import StrictUndefined, Template
from pydantic import BaseModel

from webwright import Environment, Model, __version__
from webwright.exceptions import FormatError, InterruptAgentFlow, LimitsExceeded
from webwright.utils.serialize import recursive_merge


DEFAULT_SUMMARY_USER_PROMPT = """You are about to have your working context compacted to save tokens.

Write a concise but COMPLETE summary of everything relevant from the conversation above so that a fresh
agent with only this summary (plus the original system prompt and task instructions) can continue the
task without losing progress. Include:

- The original task goal and all critical points / constraints.
- The workspace directory and key file paths (plan.md, self_reflect_config.json, final_script.py, final_runs/).
- Which critical points have been satisfied, which are still open, and any known blockers.
- Key findings from prior exploration (working selectors, URLs, ARIA labels, pitfalls to avoid).
- The latest final_runs/run_<id>/ state, most recent self_reflection verdict, and the next action to take.

Write the summary as plain prose and bullet lists. Do NOT issue a new bash_command. Do NOT set done=true.
Put the entire summary in the `thought` field (or equivalent text field) and leave action fields empty."""


class AgentConfig(BaseModel):
    system_template: str
    instance_template: str
    step_limit: int = 15
    debug_log: bool = True
    attach_instance_template_after_observation: bool = False
    attach_plan_md_after_observation: bool = False
    require_self_reflection_success: bool = False
    summary_every_n_steps: int = 0
    summary_user_prompt: str = DEFAULT_SUMMARY_USER_PROMPT
    # Strip the ARIA snapshot payload from observation messages older than the last N
    # to bound context growth in browser-driven modes. Any value <= 0 disables pruning
    # (default). Opt in per config (e.g. local_browser.yaml sets this to 1).
    keep_last_n_observations: int = -1
    output_path: Path | None = None


def _sanitize_message_for_disk(message: dict[str, Any]) -> dict[str, Any]:
    cloned = copy.deepcopy(message)
    content = cloned.get("content")
    if isinstance(content, list):
        for part in content:
            if isinstance(part, dict) and part.get("type") == "input_image":
                part["image_url"] = "<omitted:data-url>"
    return cloned


def _observation_for_markdown(observation: dict[str, Any], *, model_usage: dict[str, Any] | None = None) -> dict[str, Any]:
    cloned = copy.deepcopy(observation)
    cloned.pop("aria_snapshot", None)
    if model_usage:
        cloned["model_usage"] = copy.deepcopy(model_usage)
    return cloned


def _action_text(action: dict[str, Any]) -> str:
    return str(action.get("bash_command") or action.get("command") or action.get("python_code") or "").strip()


def _python_action_text(action: dict[str, Any]) -> str:
    return str(action.get("python_code") or "").strip()


def _markdown_code_fence_language(*, bash_command_text: str, python_code_text: str) -> str:
    if bash_command_text:
        return "bash"
    if python_code_text:
        return "python"
    return ""


class DefaultAgent:
    def __init__(self, model: Model, env: Environment, *, config_class: type = AgentConfig, **kwargs):
        self.config = config_class(**kwargs)
        self.messages: list[dict[str, Any]] = []
        self.model = model
        self.env = env
        self.extra_template_vars: dict[str, Any] = {}
        self.n_calls = 0
        self.n_format_errors = 0

    def _debug_dir(self) -> Path | None:
        if self.config.output_path is None:
            return None
        return self.config.output_path.parent / "debug"

    def _write_debug_step_artifact(
        self,
        *,
        step_index: int,
        assistant_message: dict[str, Any],
        outputs: list[dict[str, Any]] | None = None,
    ) -> None:
        if not self.config.debug_log:
            return
        debug_dir = self._debug_dir()
        if debug_dir is None:
            return
        steps_dir = debug_dir / "steps"
        steps_dir.mkdir(parents=True, exist_ok=True)

        extra = assistant_message.get("extra", {})
        actions = extra.get("actions", [])
        action_text = "\n\n".join(_action_text(action) for action in actions if _action_text(action))
        python_code_text = "\n\n".join(
            _python_action_text(action) for action in actions if _python_action_text(action)
        )
        bash_command_text = "\n\n".join(
            str(action.get("bash_command", "")).strip()
            for action in actions
            if str(action.get("bash_command", "")).strip()
        )
        code_fence_language = _markdown_code_fence_language(
            bash_command_text=bash_command_text,
            python_code_text=python_code_text,
        )
        payload = {
            "step": step_index,
            "thought": assistant_message.get("content", ""),
            "python_code": python_code_text,
            "bash_command": bash_command_text,
            "command_text": action_text,
            "raw_response": extra.get("raw_response", {}),
            "done": extra.get("done", False),
            "final_response": extra.get("final_response", ""),
            "outputs": outputs or [],
        }
        (steps_dir / f"step_{step_index:04d}.json").write_text(json.dumps(payload, indent=2))

        summary_path = debug_dir / "steps.md"
        with summary_path.open("a", encoding="utf-8") as handle:
            handle.write(f"## Step {step_index}\n\n")
            # Attach the model input only for the first step
            if step_index == 1:
                user_input_text = ""
                for msg in reversed(self.messages):
                    if msg.get("role") == "user":
                        content = msg.get("content", "")
                        if isinstance(content, list):
                            # Multi-part message: join text parts
                            parts = [p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") in ("text", "input_text")]
                            user_input_text = "\n".join(p for p in parts if p)
                        else:
                            user_input_text = str(content)
                        break
                if user_input_text:
                    handle.write("### Model Input\n\n")
                    handle.write(f"{user_input_text}\n\n")
            handle.write("### Thought\n\n")
            handle.write(f"{payload['thought']}\n\n")
            handle.write("### Generated Code\n\n")
            handle.write(f"```{code_fence_language}\n")
            handle.write(f"{payload['command_text']}\n")
            handle.write("```\n\n")
            if outputs:
                observation = outputs[0].get("observation", {})
                markdown_observation = _observation_for_markdown(
                    observation,
                    model_usage=extra.get("usage"),
                )
                handle.write("### Observation\n\n")
                handle.write("```json\n")
                handle.write(f"{json.dumps(markdown_observation, indent=2)}\n")
                handle.write("```\n\n")

    def get_template_vars(self, **kwargs) -> dict[str, Any]:
        return recursive_merge(
            self.config.model_dump(),
            self.env.get_template_vars(),
            self.model.get_template_vars(),
            {"n_model_calls": self.n_calls},
            self.extra_template_vars,
            kwargs,
        )

    def _render_template(self, template: str) -> str:
        return Template(template, undefined=StrictUndefined).render(**self.get_template_vars())

    def _plan_md_message(self) -> dict[str, Any] | None:
        workspace_dir = self.get_template_vars().get("workspace_dir")
        if not workspace_dir:
            return None
        plan_path = Path(workspace_dir) / "plan.md"
        if not plan_path.exists() or not plan_path.is_file():
            return None
        plan_text = plan_path.read_text(encoding="utf-8").strip()
        if not plan_text:
            return None
        return self.model.format_message(role="user", content=f"Current plan.md:\n\n{plan_text}")

    def _self_reflection_gate_error(self) -> str | None:
        """Return an error string if done=true should be blocked pending judge success."""
        if not self.config.require_self_reflection_success:
            return None
        return self._tool_gate_error()

    def _tool_gate_error(self) -> str | None:
        """Require final_runs/run_<latest>/self_reflect_result.json with predicted_label == 1."""
        workspace_dir = self.get_template_vars().get("workspace_dir")
        if not workspace_dir:
            return (
                "Completion blocked: require_self_reflection_success is enabled but no workspace_dir is "
                "available. Cannot locate final_runs/run_<id>/self_reflect_result.json. Do not set done=true."
            )
        final_runs_dir = Path(workspace_dir) / "final_runs"
        if not final_runs_dir.is_dir():
            return (
                "Completion blocked: no final_runs/ directory exists yet. You must run final_script.py "
                "in a final_runs/run_<id>/ folder and then run "
                "`python -m webwright.tools.self_reflection --config self_reflect_config.json "
                "--workspace-dir \"{0}\" --output final_runs/run_<id>/self_reflect_result.json` with "
                "predicted_label == 1 before setting done=true."
            ).format(workspace_dir)
        run_dirs: list[tuple[int, Path]] = []
        for entry in final_runs_dir.iterdir():
            if not entry.is_dir() or not entry.name.startswith("run_"):
                continue
            suffix = entry.name[len("run_"):]
            try:
                run_id = int(suffix)
            except ValueError:
                continue
            run_dirs.append((run_id, entry))
        if not run_dirs:
            return (
                "Completion blocked: final_runs/ contains no run_<id>/ folders. Create "
                "final_runs/run_<id>/, execute final_script.py there, then run self_reflection and "
                "only set done=true after self_reflect_result.json reports predicted_label == 1."
            )
        run_dirs.sort(key=lambda item: item[0])
        latest_run_id, latest_run_dir = run_dirs[-1]
        judge_path = latest_run_dir / "self_reflect_result.json"
        if not judge_path.is_file():
            return (
                f"Completion blocked: {judge_path} does not exist. Run "
                f"`python -m webwright.tools.self_reflection --config self_reflect_config.json "
                f"--workspace-dir \"{workspace_dir}\" --output {judge_path}` against the latest run "
                f"(run_{latest_run_id}) and only set done=true after it exits 0 with "
                f"predicted_label == 1."
            )
        try:
            judge_data = json.loads(judge_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return (
                f"Completion blocked: could not parse {judge_path}: {exc}. Re-run self_reflection "
                f"against run_{latest_run_id} and only set done=true after predicted_label == 1."
            )
        predicted_label = judge_data.get("predicted_label")
        if predicted_label != 1:
            return (
                f"Completion blocked: {judge_path} has predicted_label={predicted_label!r} "
                f"(expected 1). Diagnose the failure from self_reflect_result.json, fix final_script.py, "
                f"re-run it in a new final_runs/run_{latest_run_id + 1}/ folder, and re-run "
                f"self_reflection. Only set done=true after self_reflection exits 0 with "
                f"predicted_label == 1."
            )
        return None

    def add_messages(self, *messages: dict[str, Any]) -> list[dict[str, Any]]:
        self.messages.extend(messages)
        self._prune_old_observation_aria_snapshots()
        return list(messages)

    def _prune_old_observation_aria_snapshots(self) -> None:
        n = self.config.keep_last_n_observations
        if n <= 0:
            return
        obs_indices = [
            i for i, m in enumerate(self.messages)
            if m.get("extra", {}).get("observation")
        ]
        if len(obs_indices) <= n:
            return
        placeholder = "(ARIA snapshot pruned; see most recent observation)"
        for idx in obs_indices[:-n]:
            msg = self.messages[idx]
            obs = msg["extra"]["observation"]
            aria = obs.get("aria_snapshot", "")
            if not aria:
                continue
            content = msg.get("content")
            if isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get("type") in ("text", "input_text"):
                        text = part.get("text", "")
                        if aria in text:
                            part["text"] = text.replace(aria, placeholder)
            elif isinstance(content, str) and aria in content:
                msg["content"] = content.replace(aria, placeholder)
            obs["aria_snapshot"] = ""

    def _compact_history(self) -> None:
        """Summarize the running transcript via an LLM call and reset messages to [system, summary].

        Preserves the original system message. Replaces every non-system message with a single user
        message containing the summary. The summarization call is made with the current messages
        plus a user prompt instructing the model to produce a complete compact summary.
        """
        if not self.messages:
            return
        system_message = next((m for m in self.messages if m.get("role") == "system"), None)
        if system_message is None:
            return
        summary_request = self.model.format_message(
            role="user",
            content=self.config.summary_user_prompt,
            extra={"interrupt_type": "HistoryCompactionRequest"},
        )
        summary_messages = list(self.messages) + [summary_request]
        try:
            response = self.model.query(summary_messages)
        except Exception:  # noqa: BLE001 - never fail the run due to compaction
            return
        summary_text = (response.get("content") or "").strip()
        if not summary_text:
            extra = response.get("extra", {})
            summary_text = (extra.get("final_response") or "").strip() or "(empty summary)"
        summary_message = self.model.format_message(
            role="user",
            content=(
                "## Compacted History Summary\n"
                f"(context was compacted after step {self.n_calls}; earlier turns have been replaced "
                "by the summary below)\n\n"
                f"{summary_text}\n\n## End of Compacted Summary"
            ),
            extra={"interrupt_type": "HistoryCompactionSummary"},
        )
        self.messages = [system_message, summary_message]

    def run(self, task: str = "", **kwargs) -> dict[str, Any]:
        self.extra_template_vars |= {"task": task, **kwargs}
        self.messages = []
        self.n_calls = 0
        self.n_format_errors = 0
        self.add_messages(
            self.model.format_message(role="system", content=self._render_template(self.config.system_template)),
            self.model.format_message(role="user", content=self._render_template(self.config.instance_template)),
        )
        if self.extra_template_vars.get("explore_history"):
            self.add_messages(
                self.model.format_message(
                    role="user",
                    content="## Previous Explore History\n"
                    "Below is the message log from a prior live-browser exploration of this exact task.\n"
                    "Use it to understand the site layout, available controls, aria snapshots, and pitfalls.\n"
                    "Do NOT repeat failed approaches. Build on what was learned.\n\n"
                    + self.extra_template_vars["explore_history"]
                    + "\n\n## End of Explore History",
                ),
            )

        while True:
            try:
                self.step()
            except InterruptAgentFlow as exc:
                if isinstance(exc, FormatError):
                    self.n_format_errors += 1
                self.add_messages(*exc.messages)
            finally:
                self.save(self.config.output_path)
            if self.messages[-1].get("role") == "exit":
                break
            if (
                self.config.summary_every_n_steps > 0
                and self.n_calls > 0
                and self.n_calls % self.config.summary_every_n_steps == 0
            ):
                self._compact_history()
                self.save(self.config.output_path)
        return self.messages[-1].get("extra", {})

    def step(self) -> list[dict[str, Any]]:
        return self.execute_actions(self.query())

    def query(self) -> dict[str, Any]:
        if 0 < self.config.step_limit <= self.n_calls:
            raise LimitsExceeded(
                self.model.format_message(
                    role="exit",
                    content="Step limit exceeded.",
                    extra={"exit_status": "LimitsExceeded", "submission": ""},
                )
            )
        message = self.model.query(self.messages)
        self.n_calls += 1
        self.add_messages(message)
        return message

    def execute_actions(self, message: dict[str, Any]) -> list[dict[str, Any]]:
        extra = message.get("extra", {})
        if extra.get("done"):
            gate_error = self._self_reflection_gate_error()
            if gate_error is not None:
                extra["done"] = False
                return self.add_messages(
                    self.model.format_message(
                        role="user",
                        content=gate_error,
                        extra={"interrupt_type": "SelfReflectionGate"},
                    )
                )
            self._write_debug_step_artifact(step_index=self.n_calls, assistant_message=message, outputs=[])
            return self.add_messages(
                self.model.format_message(
                    role="exit",
                    content=extra.get("final_response", "Task completed."),
                    extra={
                        "exit_status": "Submitted",
                        "submission": extra.get("final_response", ""),
                        "final_response": extra.get("final_response", ""),
                    },
                )
            )
        outputs = [self.env.execute(action) for action in extra.get("actions", [])]
        self._write_debug_step_artifact(step_index=self.n_calls, assistant_message=message, outputs=outputs)
        observation_messages = self.model.format_observation_messages(message, outputs, self.get_template_vars())
        if self.config.attach_instance_template_after_observation:
            observation_messages.append(
                self.model.format_message(role="user", content=self._render_template(self.config.instance_template))
            )
        if self.config.attach_plan_md_after_observation:
            plan_message = self._plan_md_message()
            if plan_message is not None:
                observation_messages.append(plan_message)
        return self.add_messages(*observation_messages)

    def serialize(self, *extra_dicts) -> dict[str, Any]:
        last_message = self.messages[-1] if self.messages else {}
        last_extra = last_message.get("extra", {})
        return recursive_merge(
            {
                "info": {
                    "config": {
                        "agent": self.config.model_dump(mode="json"),
                        "agent_type": f"{self.__class__.__module__}.{self.__class__.__name__}",
                    },
                    "mini_version": __version__,
                    "exit_status": last_extra.get("exit_status", ""),
                    "submission": last_extra.get("submission", ""),
                    "api_calls": self.n_calls,
                    "format_errors": self.n_format_errors,
                },
                "messages": [_sanitize_message_for_disk(message) for message in self.messages],
                "trajectory_format": "webwright-0.1",
            },
            self.model.serialize(),
            self.env.serialize(),
            *extra_dicts,
        )

    def save(self, path: Path | None, *extra_dicts) -> dict[str, Any]:
        data = self.serialize(*extra_dicts)
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data, indent=2))
        return data
