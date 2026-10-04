from __future__ import annotations

import pytest

from webwright.models.openai_model import (
    RUN_COMMAND_TOOL,
    TOOL_RESPONSE_MODE,
    OpenAIModel,
    _normalize_response_items,
    _serialize_response_input,
    parse_tool_call_output,
)


def _payload(*output):
    return {"output": list(output)}


def _reasoning(text="thinking", encrypted="enc-blob", item_id="rs_1"):
    return {
        "type": "reasoning",
        "id": item_id,
        "encrypted_content": encrypted,
        "summary": [{"type": "summary_text", "text": text}],
    }


def _call(command='{"command":"ls -la"}', call_id="call_1"):
    return {"type": "function_call", "id": "fc_1", "call_id": call_id, "name": "run_command", "arguments": command}


def _message(text):
    return {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": text}]}


def _model(**overrides):
    return OpenAIModel(
        openai_api_key="key",
        **overrides,
    )


class TestParseToolCallOutput:
    def test_tool_call_becomes_a_command_turn(self):
        parsed = parse_tool_call_output(_payload(_reasoning(), _call()))
        assert parsed == {
            "thought": "",
            "bash_command": "ls -la",
            "python_code": "",
            "done": False,
            "final_response": "",
        }

    def test_text_without_a_tool_call_declares_completion(self):
        parsed = parse_tool_call_output(_payload(_reasoning(), _message("Final Response: 42")))
        assert parsed["done"] is True
        assert parsed["final_response"] == "Final Response: 42"
        assert parsed["bash_command"] == ""

    def test_tool_call_wins_when_text_accompanies_it(self):
        parsed = parse_tool_call_output(_payload(_message("running now"), _call()))
        assert parsed["done"] is False
        assert parsed["bash_command"] == "ls -la"

    def test_only_reasoning_is_a_format_error(self):
        with pytest.raises(ValueError, match="neither a run_command tool call nor any text"):
            parse_tool_call_output(_payload(_reasoning()))

    def test_empty_command_is_a_format_error(self):
        with pytest.raises(ValueError, match="empty command"):
            parse_tool_call_output(_payload(_call('{"command":"   "}')))

    def test_malformed_arguments_are_a_format_error(self):
        with pytest.raises(ValueError, match="not valid JSON"):
            parse_tool_call_output(_payload(_call("{not json")))

    def test_unknown_tool_is_a_format_error(self):
        call = {**_call(), "name": "rm_rf"}
        with pytest.raises(ValueError, match="Unknown tool"):
            parse_tool_call_output(_payload(call))


class TestNormalizeResponseItems:
    def test_keeps_reasoning_memory_and_the_call(self):
        items = _normalize_response_items(_payload(_reasoning(), _call()))
        assert items[0] == {
            "type": "reasoning",
            "summary": [{"type": "summary_text", "text": "thinking"}],
            "id": "rs_1",
            "encrypted_content": "enc-blob",
        }
        assert items[1] == {
            "type": "function_call",
            "call_id": "call_1",
            "name": "run_command",
            "arguments": '{"command":"ls -la"}',
        }

    def test_drops_the_transport_only_fields(self):
        item = {**_reasoning(), "status": "completed", "extra": "junk"}
        assert set(_normalize_response_items(_payload(item))[0]) == {
            "type",
            "summary",
            "id",
            "encrypted_content",
        }


class TestSerializeResponseInput:
    def test_reasoning_items_are_replayed_and_the_observation_answers_the_call(self):
        items = _normalize_response_items(_payload(_reasoning(), _call()))
        messages = [
            {"role": "system", "content": "sys", "extra": {}},
            {"role": "user", "content": "task", "extra": {}},
            {"role": "assistant", "content": "thinking", "extra": {"response_items": items, "tool_call_id": "call_1"}},
            {"role": "user", "content": "exit 0", "extra": {"tool_call_id": "call_1"}},
        ]
        serialized = _serialize_response_input(messages)
        assert [item.get("type") for item in serialized] == [
            "message",
            "message",
            "reasoning",
            "function_call",
            "function_call_output",
        ]
        assert serialized[0]["role"] == "developer"
        assert serialized[2]["encrypted_content"] == "enc-blob"
        assert serialized[4] == {"type": "function_call_output", "call_id": "call_1", "output": "exit 0"}

    def test_images_ride_along_after_the_tool_output(self):
        items = _normalize_response_items(_payload(_call()))
        observation = [
            {"type": "input_text", "text": "exit 0"},
            {"type": "input_image", "image_url": "data:image/png;base64,AAA", "detail": "high"},
        ]
        serialized = _serialize_response_input(
            [
                {"role": "assistant", "content": "", "extra": {"response_items": items}},
                {"role": "user", "content": observation, "extra": {"tool_call_id": "call_1"}},
            ]
        )
        assert serialized[1]["output"] == "exit 0"
        assert serialized[2]["content"] == [
            {"type": "input_image", "image_url": "data:image/png;base64,AAA", "detail": "high"}
        ]

    def test_unanswered_call_is_closed_before_the_next_turn(self):
        items = _normalize_response_items(_payload(_reasoning(), _call()))
        serialized = _serialize_response_input(
            [
                {"role": "assistant", "content": "", "extra": {"response_items": items}},
                {"role": "user", "content": "Format error: try again", "extra": {}},
            ]
        )
        assert [item.get("type") for item in serialized] == [
            "reasoning",
            "function_call",
            "function_call_output",
            "message",
        ]
        assert serialized[2]["output"] == "The command was not executed."

    def test_trailing_unanswered_call_is_closed(self):
        items = _normalize_response_items(_payload(_call()))
        serialized = _serialize_response_input(
            [{"role": "assistant", "content": "", "extra": {"response_items": items}}]
        )
        assert serialized[-1]["type"] == "function_call_output"

    def test_other_response_modes_are_untouched(self):
        serialized = _serialize_response_input(
            [
                {"role": "assistant", "content": "thought", "extra": {"raw_response": {"done": False}}},
                {"role": "user", "content": "obs", "extra": {}},
            ]
        )
        assert serialized[0]["content"] == [{"type": "output_text", "text": '{"done":false}'}]
        assert serialized[1]["content"] == [{"type": "input_text", "text": "obs"}]


class TestBuildPayload:
    def test_tool_mode_requests_the_tool_and_encrypted_reasoning(self):
        payload = _model(reasoning_effort="xhigh")._build_payload([{"role": "user", "content": "go", "extra": {}}])
        assert payload["tools"] == [RUN_COMMAND_TOOL]
        assert payload["parallel_tool_calls"] is False
        assert payload["include"] == ["reasoning.encrypted_content"]
        assert payload["store"] is False
        assert payload["reasoning"] == {"effort": "xhigh", "summary": "auto"}
        assert "text" not in payload
        assert "tool_choice" not in payload

    def test_compaction_turn_disables_the_tool(self):
        payload = _model()._build_payload([{"role": "user", "content": "summarize", "extra": {"disable_tools": True}}])
        assert payload["tool_choice"] == "none"

    def test_json_schema_mode_keeps_its_old_payload(self):
        payload = OpenAIModel(openai_api_key="key", response_mode="json_schema")._build_payload(
            [{"role": "user", "content": "go", "extra": {}}]
        )
        assert "tools" not in payload
        assert payload["text"]["format"]["type"] == "json_schema"


_OBSERVATION = {
    "success": True,
    "url": "https://example.com",
    "title": "Example",
    "exception": "",
    "console_output": "",
    "aria_snapshot": "",
    "screenshot_path": "",
}


class TestObservationTagging:
    def test_observation_is_tagged_with_the_call_it_answers(self):
        model = _model()
        assistant = {"role": "assistant", "content": "", "extra": {"tool_call_id": "call_1"}}
        observations = model.format_observation_messages(
            assistant,
            [{"observation": _OBSERVATION}],
        )
        assert observations[0]["extra"]["tool_call_id"] == "call_1"

    def test_observation_is_untagged_without_a_call(self):
        model = _model()
        observations = model.format_observation_messages(
            {"role": "assistant", "content": "", "extra": {}},
            [{"observation": _OBSERVATION}],
        )
        assert "tool_call_id" not in observations[0]["extra"]


def test_tool_query_execute_observe_and_complete(monkeypatch, tmp_path):
    from webwright.environments.local_workspace import LocalWorkspaceEnvironment
    model = _model(observation_template='Exit={{ observation.returncode }}\n{{ observation.command_output }}')
    responses = iter([_payload(_reasoning(), _call('{"command":"printf hello"}')),
                      _payload(_message('Final Response: hello'))])
    requests = []
    async def post(payload):
        requests.append(payload)
        return next(responses)
    monkeypatch.setattr(model, '_post_with_retries', post)
    messages = [{'role': 'user', 'content': 'say hello'}]
    turn = model.query(messages)
    assert turn['extra']['done'] is False
    env = LocalWorkspaceEnvironment(output_dir=tmp_path)
    env.prepare(task='say hello')
    result = env.execute(turn['extra']['actions'][0])
    observations = model.format_observation_messages(turn, [result])
    final = model.query([*messages, turn, *observations])
    tool_result = next(item for item in requests[1]['input'] if item['type'] == 'function_call_output')
    assert next(item for item in requests[1]['input'] if item['type'] == 'reasoning')['encrypted_content'] == 'enc-blob'
    assert tool_result['call_id'] == 'call_1'
    assert 'hello' in tool_result['output']
    assert final['extra']['done'] is True
    assert final['content'] == 'Final Response: hello'


def test_new_config_renders_and_uses_browserbase(monkeypatch):
    from webwright.config import get_config_from_spec
    from webwright.utils.serialize import recursive_merge
    from jinja2 import Template, StrictUndefined
    for name, mode in [('best_default_judge_json_persistent_cli.yaml', 'run_command_tool')]:
        config = recursive_merge(get_config_from_spec(name), get_config_from_spec('model_openai.yaml'))
        model = OpenAIModel(openai_api_key='key', **{k: v for k, v in config['model'].items() if k != 'model_class'})
        assert model.config.response_mode == mode
        assert config['environment']['browser_mode'] == 'browserbase'
        assert config['agent']['trajectory_reflection_config'] == 'judge_config.json'
        prompt = Template(config['agent']['system_template'], undefined=StrictUndefined).render(workspace_dir='/tmp/task', start_url='https://example.test')
        assert 'webwright.tools.image_read' in prompt
        assert '--scope trajectory' in prompt
        assert '/home/luyadong' not in prompt


@pytest.mark.parametrize("mode", [TOOL_RESPONSE_MODE, "json_schema"])
def test_base_prompt_matches_response_mode(mode):
    from jinja2 import StrictUndefined, Template
    from webwright.config import get_config_from_spec
    from webwright.utils.serialize import recursive_merge

    config = recursive_merge(get_config_from_spec("base.yaml"), get_config_from_spec("model_openai.yaml"))
    model = OpenAIModel(openai_api_key="key", response_mode=mode)
    variables = dict(model.get_template_vars(), start_url="https://example.com", workspace_dir="/tmp/task")
    prompt = Template(config["agent"]["system_template"], undefined=StrictUndefined).render(**variables)
    if mode == TOOL_RESPONSE_MODE:
        assert "exactly one `run_command` tool call" in prompt
        assert "single strict JSON object" not in prompt
        assert 'Set `"done": true`' not in prompt
    else:
        assert "single strict JSON object" in prompt
