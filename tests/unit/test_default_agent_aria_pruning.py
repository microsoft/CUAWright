from typing import Any

from webwright.agents.default import DefaultAgent


class StubModel:
    def __init__(self) -> None:
        self.queries: list[list[dict[str, Any]]] = []

    def format_message(self, role: str, content: str, **kwargs: Any) -> dict[str, Any]:
        return {"role": role, "content": content, "extra": kwargs.get("extra", {})}

    def get_template_vars(self) -> dict[str, Any]:
        return {}

    def query(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        self.queries.append(messages)
        return self.format_message(role="assistant", content="summary")


class StubEnv:
    def get_template_vars(self) -> dict[str, Any]:
        return {}


def make_agent() -> DefaultAgent:
    return DefaultAgent(
        StubModel(),
        StubEnv(),
        system_template="system",
        instance_template="instance",
        keep_last_n_observations=1,
    )


def observation_message(label: str, aria_snapshot: str, content: Any | None = None) -> dict[str, Any]:
    if content is None:
        content = f"Observation {label}\n{aria_snapshot}"
    return {
        "role": "user",
        "content": content,
        "extra": {
            "observation": {
                "label": label,
                "aria_snapshot": aria_snapshot,
            }
        },
    }


def test_prunes_old_observation_aria_snapshots_only() -> None:
    agent = make_agent()
    first = observation_message("first", "ARIA_FIRST")
    middle = {"role": "assistant", "content": "no observation", "extra": {}}
    second = observation_message(
        "second",
        "ARIA_SECOND",
        content=[{"type": "text", "text": "Observation second\nARIA_SECOND"}],
    )
    third = observation_message("third", "ARIA_THIRD")

    agent.add_messages(first, middle, second, third)
    agent._prune_old_observation_aria_snapshots()

    assert first["extra"]["observation"]["aria_snapshot"] == ""
    assert "ARIA_FIRST" not in first["content"]
    assert "(ARIA snapshot pruned; see most recent observation)" in first["content"]

    assert middle["content"] == "no observation"

    assert second["extra"]["observation"]["aria_snapshot"] == ""
    assert "ARIA_SECOND" not in second["content"][0]["text"]
    assert "(ARIA snapshot pruned; see most recent observation)" in second["content"][0]["text"]

    assert third["extra"]["observation"]["aria_snapshot"] == "ARIA_THIRD"
    assert "ARIA_THIRD" in third["content"]


def test_compaction_resets_incremental_pruning_state() -> None:
    agent = make_agent()
    system = agent.model.format_message(role="system", content="system")
    first = observation_message("first", "ARIA_FIRST")
    second = observation_message("second", "ARIA_SECOND")

    agent.add_messages(system, first, second)
    assert first["extra"]["observation"]["aria_snapshot"] == ""
    assert second["extra"]["observation"]["aria_snapshot"] == "ARIA_SECOND"

    agent._compact_history()

    third = observation_message("third", "ARIA_THIRD")
    fourth = observation_message("fourth", "ARIA_FOURTH")
    agent.add_messages(third, fourth)

    assert third["extra"]["observation"]["aria_snapshot"] == ""
    assert fourth["extra"]["observation"]["aria_snapshot"] == "ARIA_FOURTH"
