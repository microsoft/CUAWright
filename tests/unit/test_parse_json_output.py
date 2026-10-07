import json
import pytest

from webwright.models.base import parse_json_output


def test_valid_json_roundtrip() -> None:
    """Valid JSON object with done=False is returned unchanged."""
    raw = json.dumps({"bash_command": "", "done": False, "summary": "hello"})
    result = parse_json_output(raw)
    assert result == {"bash_command": "", "done": False, "summary": "hello"}


def test_done_demoted_when_action_is_nonempty() -> None:
    """done=true is demoted to False when bash_command is non-empty."""
    raw = json.dumps({"bash_command": "ls -la", "done": True})
    result = parse_json_output(raw)
    assert result["done"] is False
    assert result["bash_command"] == "ls -la"


def test_done_preserved_when_action_is_empty() -> None:
    """done=true remains True when bash_command is empty/absent."""
    raw = json.dumps({"done": True, "summary": "all done"})
    result = parse_json_output(raw)
    assert result["done"] is True


def test_non_dict_json_raises_value_error() -> None:
    """JSON that is not an object (e.g. a list) raises ValueError."""
    raw = json.dumps(["step1", "step2"])
    with pytest.raises(ValueError, match="not a JSON object"):
        parse_json_output(raw)


def test_malformed_json_raises_value_error() -> None:
    """Malformed JSON raises ValueError."""
    with pytest.raises(ValueError, match="Unable to parse JSON output"):
        parse_json_output("{not valid json")


def test_custom_action_field() -> None:
    """custom action_field parameter is used for done-demotion logic."""
    raw = json.dumps({"tool_call": "run_script.py", "done": True})
    result = parse_json_output(raw, action_field="tool_call")
    assert result["done"] is False
    assert result["tool_call"] == "run_script.py"
