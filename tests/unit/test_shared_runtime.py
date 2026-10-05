"""Behavioral coverage for unified scheduling and the retained browser runtime."""

import copy
import importlib
import json
import subprocess
import sys

import pytest

from cuawright.agents.default import CallBudget, run_loop


class Model:
    def __init__(self, turns):
        self.turns = iter(turns)

    def format_message(self, **kwargs):
        return {"extra": {}, **kwargs}

    def query(self, messages):
        turn = next(self.turns)
        if isinstance(turn, Exception):
            raise turn
        return copy.deepcopy(turn)

    def format_observation_messages(self, message, outputs, template_vars):
        return [self.format_message(role="user", content=json.dumps(outputs))]

    def get_template_vars(self):
        return {}

    def serialize(self):
        return {}


class Workspace:
    def __init__(self):
        self.commands = []

    def execute(self, action):
        self.commands.append(action["bash_command"])
        return {"command_output": "hello", "returncode": 0}

    def get_template_vars(self):
        return {}

    def serialize(self):
        return {}


def turn(done=False):
    return {
        "role": "assistant",
        "content": "",
        "extra": {
            "actions": [] if done else [{"bash_command": "printf hello"}],
            "done": done,
            "final_response": "hello" if done else "",
        },
    }


@pytest.mark.parametrize("scenario", ["submit", "limit", "format", "gate", "compact"])
def test_new_browser_matches_retained_runtime(scenario):
    records = []
    for namespace, module in (
        ("webwright", "agents.default"),
        ("cuawright", "agents.browser"),
    ):
        agent_class = importlib.import_module(f"{namespace}.{module}").DefaultAgent
        exceptions = importlib.import_module(f"{namespace}.exceptions")
        turns = [turn(), turn(done=True)]
        kwargs = {"step_limit": 2}
        if scenario == "limit":
            kwargs["step_limit"] = 1
        elif scenario == "format":
            turns.insert(
                0,
                exceptions.FormatError(
                    {"role": "user", "content": "retry", "extra": {}}
                ),
            )
        elif scenario == "gate":
            turns = [turn(done=True), turn()]
            kwargs["require_self_reflection_success"] = True
        elif scenario == "compact":
            turns.insert(1, {"role": "assistant", "content": "handoff", "extra": {}})
            kwargs["summary_every_n_steps"] = 1
        env = Workspace()
        agent = agent_class(
            Model(turns),
            env,
            system_template="system",
            instance_template="{{task}}",
            debug_log=False,
            **kwargs,
        )
        result = agent.run("task")
        records.append(
            (result, agent.messages, agent.n_calls, agent.n_format_errors, env.commands)
        )
    assert records[0] == records[1]


def test_browser_persists_partial_trajectory_on_failure(tmp_path):
    from cuawright.agents.browser import DefaultAgent

    path = tmp_path / "trajectory.json"
    agent = DefaultAgent(
        Model([RuntimeError("transport unavailable")]),
        Workspace(),
        system_template="system",
        instance_template="task",
        output_path=path,
    )
    with pytest.raises(RuntimeError, match="transport unavailable"):
        agent.run()
    assert [row["role"] for row in json.loads(path.read_text())["messages"]] == [
        "system",
        "user",
    ]


def test_shared_loop_skips_maintenance_on_incomplete_and_submitted_turns():
    events = []

    class Adapter:
        budget = CallBudget(3)

        def loop_ready(self):
            return not self.budget.exhausted

        def loop_step(self):
            self.budget.charge()
            events.append(("step", self.budget.used))
            return self.budget.used != 1

        def loop_done(self):
            return self.budget.used == 3

        def loop_maintain(self):
            events.append(("compact", self.budget.used))

        def loop_result(self):
            return {"calls": self.budget.used}

    assert run_loop(Adapter()) == {"calls": 3}
    assert events == [("step", 1), ("step", 2), ("compact", 2), ("step", 3)]
    unlimited = CallBudget(None)
    unlimited.charge()
    assert unlimited.remaining is None and not unlimited.exhausted


@pytest.mark.parametrize("backend", ["web", "desktop"])
def test_unified_backend_help(backend):
    result = subprocess.run(
        [sys.executable, "-m", "cuawright", backend, "--help"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "Usage" in result.stdout or "usage" in result.stdout


def test_unknown_backend_fails():
    result = subprocess.run(
        [sys.executable, "-m", "cuawright", "unknown"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 2
