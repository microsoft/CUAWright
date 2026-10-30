import contextlib
import importlib
import io
import json
import logging
import math
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from ...agents.default import Actor, RETRY_DELAYS
from ....core.responses import responses_url
from ...utils.artifacts import (
    Store,
    credentials,
    digest,
    file_hash,
    harness_provenance,
    inventory,
)
from ...config import prompts
from ...environments.osworld.compatibility import install as install_compatibility
from ...environments.osworld.compatibility import patch_task
from ...environments.osworld.guest_tools import CLIENT_HASHES, Guest
from ...environments.osworld.source import (
    activate,
    configure_external,
    load_task,
    restore_external,
)
from ...models.openai_response_model import create_client
from ...exceptions import ReleaseError
from ...models.utils.actions_toolcall_response import TOOLS


@dataclass(frozen=True, kw_only=True)
class RuntimeSettings:
    source: Path
    vm: Path
    credentials: Path
    results: Path
    model: str
    responses_url: str = "https://api.openai.com/v1/responses"
    reasoning: str = "xhigh"
    steps: int = 300
    compact_every: int = 40
    max_output_tokens: int = 32768
    website_host_suffix: str = ""

    def validate_runtime(self):
        if type(self.steps) is not int or not 1 <= self.steps <= 500:
            raise ReleaseError("steps must be in 1..500")
        if (
            type(self.compact_every) is not int
            or self.compact_every < 0
            or type(self.max_output_tokens) is not int
            or not 1 <= self.max_output_tokens <= 32768
        ):
            raise ReleaseError("invalid compaction or output token limit")
        try:
            responses_url(self.responses_url)
        except ValueError as exc:
            raise ReleaseError(str(exc)) from None
        if not self.model.strip():
            raise ReleaseError("model must be nonempty")
        if not self.source.is_dir() or self.source.is_symlink():
            raise ReleaseError("source must be a real directory")
        for path in (self.vm, self.credentials):
            if not path.is_file() or path.is_symlink():
                raise ReleaseError("VM and credentials must be nonsymlink files")
        if self.results.exists() or self.results.is_symlink():
            raise ReleaseError("results directory must not already exist")
        if not self.results.parent.is_dir():
            raise ReleaseError("results parent must exist")
        if self.credentials.resolve().is_relative_to(self.results.resolve()):
            raise ReleaseError("credentials must remain external")

    def public(self):
        values = asdict(self)
        values.pop("credentials")
        values.pop("responses_url")
        return {
            key: str(value) if isinstance(value, Path) else value
            for key, value in values.items()
        }


@dataclass(frozen=True, kw_only=True)
class Settings(RuntimeSettings):
    tasks: Path
    assets: Path
    task_id: str
    proxy_config: Path | None = None

    def validate(self):
        self.validate_runtime()
        for path in (self.tasks, self.assets):
            if not path.is_dir() or path.is_symlink():
                raise ReleaseError("tasks and assets must be real directories")
        if self.proxy_config is not None and (
            not self.proxy_config.is_file() or self.proxy_config.is_symlink()
        ):
            raise ReleaseError("proxy config must be a nonsymlink file")
        protected = [path.resolve() for path in (self.source, self.tasks, self.assets)]
        if any(self.results.resolve().is_relative_to(path) for path in protected):
            raise ReleaseError(
                "results must be separate from source, tasks, and assets"
            )
        if self.credentials.resolve().is_relative_to(self.results.resolve()):
            raise ReleaseError("credentials must remain external")
        if any(self.credentials.resolve().is_relative_to(path) for path in protected):
            raise ReleaseError("credentials must be outside source, tasks, and assets")

    def public(self):
        values = super().public()
        values.pop("proxy_config")
        return values


@dataclass(frozen=True, kw_only=True)
class CustomSettings(RuntimeSettings):
    instruction: str

    def validate(self):
        self.validate_runtime()
        if not self.instruction.strip() or len(self.instruction) > 20_000:
            raise ReleaseError("instruction must contain 1..20000 characters")
        if self.results.resolve().is_relative_to(self.source.resolve()):
            raise ReleaseError("results must be separate from source")


@contextlib.contextmanager
def quiet_external():
    # Official setup/evaluator diagnostics can contain hidden task state.
    previous = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        with (
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            yield
    finally:
        logging.disable(previous)


def task_phases(task):
    getter = getattr(task, "get_phases", None)
    if not callable(getter):
        return []
    phases = getter()
    if not isinstance(phases, list) or not phases:
        raise ReleaseError("multi-phase task must provide a nonempty phase list")
    for item in phases:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("instruction"), str)
            or not item["instruction"].strip()
            or not callable(item.get("setup"))
            or not callable(item.get("evaluate"))
        ):
            raise ReleaseError("invalid multi-phase task contract")
    return phases


def score_value(evaluation):
    score = evaluation.get("score") if isinstance(evaluation, dict) else evaluation
    if (
        type(score) not in (int, float)
        or not math.isfinite(score)
        or not 0 <= score <= 1
    ):
        raise ReleaseError("official evaluator returned invalid score")
    return float(score)


def retryable_evaluation(exc):
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status in (400, 408, 429) or status >= 500
    return type(exc).__name__ in {
        "APIConnectionError",
        "APITimeoutError",
        "RateLimitError",
        "ReadTimeout",
        "ConnectionError",
    }


def evaluate(callback, store):
    for attempt in range(10):
        try:
            with quiet_external():
                return score_value(callback())
        except Exception as exc:
            if not retryable_evaluation(exc) or attempt == 9:
                raise
            store.event(
                "evaluation_retry",
                {
                    "attempt": attempt + 1,
                    "exception_type": type(exc).__name__,
                    "status_code": getattr(exc, "status_code", None),
                    "next_delay_seconds": RETRY_DELAYS[attempt],
                },
            )
            time.sleep(RETRY_DELAYS[attempt])
    raise ReleaseError("evaluation retry loop exhausted")


def opening_instruction(phases):
    schedule = "\n".join(
        f"{index}. {item.get('name', f'Phase {index}')} "
        f"(weight {float(item.get('weight', 1.0)):.2f})"
        for index, item in enumerate(phases, 1)
    )
    return (
        "This is a sequential multi-phase task. One shared model-call budget "
        "covers every phase and is never reset. Reserve enough calls for later "
        "phases. Submitting the current phase triggers its evaluation and the "
        "next phase setup.\n\nPhase schedule:\n" + schedule
    )


def run_phases(actor, guest, env, task, phases):
    phase_results = []
    total_score = 0.0
    outcome = actor.outcome("budget_exhausted")
    use_proxy = bool(task.get("proxy", False) and env.enable_proxy)
    for index, item in enumerate(phases, 1):
        if index > 1:
            env._step_no = 0
            env.action_history.clear()
            env._traj_no += 1
            with quiet_external():
                item["setup"](env.setup_controller, use_proxy=use_proxy)
            env.is_environment_used = True
            pause = float(item.get("pause_after_setup_seconds", 5) or 0)
            if pause:
                time.sleep(pause)
            guest.submitted = False
        instruction = item["instruction"]
        env.instruction = instruction
        actor.set_phase(
            prompts.phase(
                instruction,
                f"phase-{index}",
                index,
                len(phases),
                previous_evaluated=index > 1,
            )
        )
        outcome = actor.run()
        env.action_history.append("DONE")
        phase_score = evaluate(lambda: item["evaluate"](env), actor.store)
        total_score += phase_score
        phase_results.append(
            {
                "phase_index": index,
                "phase_name": item.get("name", f"Phase {index}"),
                "score": phase_score,
                "model_calls": outcome["model_calls"],
                "commands": outcome["commands"],
            }
        )
        gate = item.get("gate_min_score")
        if gate is not None and phase_score < float(gate):
            break
        if outcome["stop_reason"] != "submitted" or not actor.remaining:
            break
    outcome["score"] = round(max(0.0, min(1.0, total_score)), 4)
    outcome["phases"] = phase_results
    return outcome


def run(settings, client_factory=create_client, guest_factory=Guest):
    settings.validate()
    store = Store(settings.results)
    env = None
    client = None
    actor = None
    previous = {}
    failure = None
    outcome = None
    stage = "preflight"
    try:
        external = activate(settings.source)
        key = credentials(settings.credentials)
        previous = configure_external(
            settings.assets.resolve(),
            key,
            settings.responses_url,
            settings.website_host_suffix,
            settings.proxy_config,
        )
        # OSWorld reads PROXY_CONFIG_FILE when desktop_env.controllers.setup is
        # imported, so the environment must be configured first.
        install_compatibility()
        with quiet_external():
            task, instruction, task_manifest = load_task(
                settings.tasks, settings.task_id
            )
            phases = task_phases(task)
        evaluator_image_max = patch_task(task)
        proxy_requested = bool(task.get("proxy", False))
        if proxy_requested and settings.proxy_config is None:
            raise ReleaseError("proxy task requires an external proxy config")
        actor_instruction = opening_instruction(phases) if phases else instruction
        manifest = {
            "condition": "osworld-minimal-reliability-v1",
            "harness": harness_provenance(),
            "external": external,
            "task": task_manifest,
            "assets": inventory(settings.assets),
            "vm_sha256": file_hash(settings.vm),
            "proxy": {
                "requested": proxy_requested,
                "enabled": proxy_requested,
                "config_sha256": (
                    file_hash(settings.proxy_config)
                    if settings.proxy_config is not None
                    else None
                ),
            },
            "compatibility": {"evaluator_image_max": evaluator_image_max},
            "settings": settings.public(),
            "prompts": {
                "actor": digest(prompts.ACTOR.encode()),
                "compaction": digest(prompts.COMPACTION.encode()),
                "initial": digest(prompts.initial(actor_instruction).encode()),
                "phases": [
                    digest(
                        prompts.phase(
                            item["instruction"],
                            f"phase-{index}",
                            index,
                            len(phases),
                            previous_evaluated=index > 1,
                        ).encode()
                    )
                    for index, item in enumerate(phases, 1)
                ],
                "module": file_hash(Path(prompts.__file__)),
            },
            "tools": digest(json.dumps(TOOLS, sort_keys=True).encode()),
            "guest_clients": CLIENT_HASHES,
        }
        store.write("manifest.json", manifest)
        stage = "desktop_initialization"
        with quiet_external():
            desktop_class = importlib.import_module(
                "desktop_env.desktop_env"
            ).DesktopEnv
            # Retain the partially constructed object for cleanup if __init__ fails.
            env = desktop_class.__new__(desktop_class)
            desktop_class.__init__(
                env,
                provider_name="docker",
                path_to_vm=str(settings.vm.resolve()),
                os_type="Ubuntu",
                action_space="pyautogui",
                cache_dir=str(store.root / "evaluator-cache"),
                headless=True,
                require_a11y_tree=True,
                enable_proxy=proxy_requested,
                volume_size=task.get("volume_size") or 80,
                force_disable_recording=True,
            )
        guest = guest_factory(env)
        guest.website_host_suffix = settings.website_host_suffix
        stage = "control_provision"
        guest.provision_controls()
        stage = "task_setup"
        with quiet_external():
            env.reset(task_config=task)
        stage = "support_provision"
        guest.provision_support()
        client = client_factory(key, settings)
        stage = "actor"
        actor = Actor(client, guest, store, actor_instruction, settings)
        if phases:
            outcome = run_phases(actor, guest, env, task, phases)
        else:
            outcome = actor.run()
            stage = "evaluation"
            outcome["score"] = evaluate(env.evaluate, store)
        stage = "evaluation"
        stage = "persistence"
    except BaseException as exc:
        failure = exc
    finally:
        cleanup_failed = False
        if env is not None:
            try:
                with quiet_external():
                    env.close()
            except BaseException:
                cleanup_failed = True
        if client is not None:
            try:
                client.close()
            except BaseException:
                cleanup_failed = True
        restore_external(previous)
        if cleanup_failed:
            failure = ReleaseError("resource cleanup failed")
            stage = "cleanup"
        if failure is None:
            try:
                store.event("cleanup", {"status": "completed"})
                store.write("result.json", {"status": "completed", **outcome})
            except BaseException as exc:
                failure = exc
                stage = "persistence"
        if failure is not None:
            # Never serialize exception text, task objects, or evaluator payloads.
            try:
                store.write(
                    "result.json",
                    {
                        "status": "failed",
                        "stage": stage,
                        "error_type": type(failure).__name__,
                        "cleanup_failed": cleanup_failed,
                        "actor": actor.outcome("failed") if actor is not None else None,
                    },
                )
            except BaseException:
                raise ReleaseError("failure result persistence failed") from None
    if failure is not None:
        raise ReleaseError(f"release failed during {stage}") from None
    return outcome


def run_custom(settings, client_factory=create_client, guest_factory=Guest):
    settings.validate()
    store = Store(settings.results)
    env = None
    client = None
    actor = None
    previous = {}
    failure = None
    outcome = None
    stage = "preflight"
    try:
        external = activate(settings.source)
        key = credentials(settings.credentials)
        previous = configure_external(
            None, key, settings.responses_url, settings.website_host_suffix
        )
        install_compatibility()
        manifest = {
            "condition": "osworld-custom-task-v1",
            "harness": harness_provenance(),
            "external": external,
            "vm_sha256": file_hash(settings.vm),
            "settings": settings.public(),
            "prompts": {
                "actor": digest(prompts.ACTOR.encode()),
                "compaction": digest(prompts.COMPACTION.encode()),
                "initial": digest(prompts.initial(settings.instruction).encode()),
                "module": file_hash(Path(prompts.__file__)),
            },
            "tools": digest(json.dumps(TOOLS, sort_keys=True).encode()),
            "guest_clients": CLIENT_HASHES,
        }
        store.write("manifest.json", manifest)
        stage = "desktop_initialization"
        with quiet_external():
            desktop_class = importlib.import_module(
                "desktop_env.desktop_env"
            ).DesktopEnv
            env = desktop_class.__new__(desktop_class)
            desktop_class.__init__(
                env,
                provider_name="docker",
                path_to_vm=str(settings.vm.resolve()),
                os_type="Ubuntu",
                action_space="pyautogui",
                cache_dir=str(store.root / "runtime-cache"),
                headless=True,
                require_a11y_tree=True,
                enable_proxy=False,
                volume_size=80,
                force_disable_recording=True,
            )
        guest = guest_factory(env)
        guest.website_host_suffix = settings.website_host_suffix
        stage = "control_provision"
        guest.provision_controls()
        stage = "task_setup"
        task = {
            "id": "custom",
            "instruction": settings.instruction,
            "platform": "linux",
            "config": [],
            "disable_recording": True,
        }
        with quiet_external():
            env.reset(task_config=task)
        stage = "support_provision"
        guest.provision_support()
        client = client_factory(key, settings)
        stage = "actor"
        actor = Actor(client, guest, store, settings.instruction, settings)
        outcome = actor.run()
        stage = "persistence"
    except BaseException as exc:
        failure = exc
    finally:
        cleanup_failed = False
        if env is not None:
            try:
                with quiet_external():
                    env.close()
            except BaseException:
                cleanup_failed = True
        if client is not None:
            try:
                client.close()
            except BaseException:
                cleanup_failed = True
        restore_external(previous)
        if cleanup_failed:
            failure = ReleaseError("resource cleanup failed")
            stage = "cleanup"
        if failure is None:
            try:
                store.event("cleanup", {"status": "completed"})
                store.write("result.json", {"status": "completed", **outcome})
            except BaseException as exc:
                failure = exc
                stage = "persistence"
        if failure is not None:
            try:
                store.write(
                    "result.json",
                    {
                        "status": "failed",
                        "stage": stage,
                        "error_type": type(failure).__name__,
                        "cleanup_failed": cleanup_failed,
                        "actor": actor.outcome("failed") if actor is not None else None,
                    },
                )
            except BaseException:
                raise ReleaseError("failure result persistence failed") from None
    if failure is not None:
        raise ReleaseError(f"custom task failed during {stage}") from None
    return outcome
