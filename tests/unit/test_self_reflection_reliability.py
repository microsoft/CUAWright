import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from cuawright.webwright.tools import self_reflection as judge


def run_judge(monkeypatch, tmp_path, responses):
    calls = []
    replies = iter(responses)

    def call(**kwargs):
        calls.append(kwargs)
        return next(replies)

    monkeypatch.setattr(judge, '_call_model', call)
    monkeypatch.setattr(judge, '_model_endpoint', lambda model: 'test')
    image = tmp_path / 'image.png'
    image.write_bytes(b'image')
    kwargs = dict(images=[image], image_judge_system_prompt='score',
                  image_judge_user_prompt='requirements', final_verdict_system_prompt='verdict',
                  final_verdict_user_prompt='{action_history_log}\n{image_reasonings}',
                  action_history_log='actions', max_image_parse_retries=2,
                  final_max_new_tokens=100, image_max_new_tokens=100,
                  model_client=SimpleNamespace(config=SimpleNamespace(model_name='test')))
    return calls, kwargs


def test_failed_image_judgment_prevents_final_call(monkeypatch, tmp_path):
    calls, kwargs = run_judge(monkeypatch, tmp_path, ['invalid', 'invalid'])
    with pytest.raises(ValueError, match='Incomplete image judgment'):
        asyncio.run(judge.run_self_reflection_async(**kwargs))
    assert len(calls) == 2


def test_malformed_verdict_is_retried(monkeypatch, tmp_path):
    calls, kwargs = run_judge(monkeypatch, tmp_path,
                             ['Reasoning: satisfied\nScore: 5', 'unknown', 'Status: success'])
    result = asyncio.run(judge.run_self_reflection_async(**kwargs))
    assert result.predicted_label == 1
    assert "endpoint" not in result.to_dict()
    assert len(calls) == 3
    assert calls[-1]['user_content'][1]['type'] == 'input_image'


def test_exhausted_verdict_is_evaluation_error(monkeypatch, tmp_path):
    calls, kwargs = run_judge(monkeypatch, tmp_path,
                             ['Reasoning: satisfied\nScore: 5', 'unknown', 'unknown'])
    with pytest.raises(ValueError, match='no valid Status'):
        asyncio.run(judge.run_self_reflection_async(**kwargs))


def test_valid_failure_is_preserved(monkeypatch, tmp_path):
    calls, kwargs = run_judge(monkeypatch, tmp_path,
                             ['Reasoning: unmet\nScore: 1', 'Status: failure'])
    assert asyncio.run(judge.run_self_reflection_async(**kwargs)).predicted_label == 0
    assert len(calls) == 2


@pytest.mark.parametrize('record', [dict(Score=True, Reasoning='x'),
    dict(Score=5, Reasoning=''), dict(Score=5, Reasoning='x', ParseFailed=True)])
def test_invalid_records_are_rejected(record):
    with pytest.raises(ValueError):
        judge.validate_image_judge_records(['image'], [record])


def test_missing_records_are_rejected():
    with pytest.raises(ValueError, match='different lengths'):
        judge.validate_image_judge_records(['image'], [])


def test_trajectory_cli_includes_unreferenced_images_and_fingerprints(monkeypatch, tmp_path):
    (tmp_path / 'screenshots').mkdir()
    image = tmp_path / 'screenshots' / 'manual.png'
    image.write_bytes(b'image')
    (tmp_path / 'plan.md').write_text('requirements')
    (tmp_path / 'command_history.sh').write_text('Final Response: 42')
    config = tmp_path / 'judge.json'
    config.write_text(json.dumps({key: 'prompt' for key, _ in judge._PROMPT_FIELDS}))
    captured = {}
    def reflect(**kwargs):
        captured.update(kwargs)
        return judge.SelfReflectionResult([], [str(image)], '', '', 'Status: success', 1)
    monkeypatch.setattr(judge, 'run_self_reflection', reflect)
    monkeypatch.setattr(judge, 'load_tool_model', lambda **kwargs: object())
    out = tmp_path / 'reflection' / 'result.json'
    assert judge.main(['--scope', 'trajectory', '--config', str(config),
                       '--workspace-dir', str(tmp_path), '--output', str(out)]) == 0
    assert captured['images'] == [image]
    assert captured['action_history_log'] == 'Final Response: 42'
    result = json.loads(out.read_text())
    assert result['evidence_digest'] and result['plan_digest'] and result['config_digest']


def passing_gate(tmp_path):
    from cuawright.webwright.agents.default import DefaultAgent
    from cuawright.webwright.environments.local_workspace import LocalWorkspaceEnvironment
    from cuawright.webwright.models.openai_model import OpenAIModel
    from cuawright.webwright.utils.browser_evidence import optional_file_digest, trajectory_evidence_digest
    env = LocalWorkspaceEnvironment(output_dir=tmp_path)
    env.prepare(task='test')
    image = tmp_path / 'screenshots' / 'state.png'
    image.write_bytes(b'original')
    (tmp_path / 'plan.md').write_text('requirements')
    config = tmp_path / 'self_reflect_config.json'
    config.write_text('{}')
    agent = DefaultAgent(OpenAIModel(openai_api_key='key'), env,
                         system_template='system', instance_template='task',
                         require_self_reflection_success=True, self_reflection_scope='trajectory')
    result = dict(predicted_label=1, final_response='Status: success', image_paths=[str(image)],
                  image_records=[dict(Score=5, Reasoning='verified')],
                  evidence_digest=trajectory_evidence_digest(tmp_path, []),
                  action_history_digest=optional_file_digest(tmp_path / 'command_history.sh'),
                  plan_digest=optional_file_digest(tmp_path / 'plan.md'),
                  config_digest=optional_file_digest(config))
    path = tmp_path / 'reflection' / 'judge_result.json'
    path.parent.mkdir()
    path.write_text(json.dumps(result))
    return agent, path, result


def test_trajectory_gate_accepts_current_complete_pass(tmp_path):
    agent, _, _ = passing_gate(tmp_path)
    assert agent._self_reflection_gate_error() is None


@pytest.mark.parametrize('changed', ['screenshots/state.png', 'screenshots/new.png',
    'plan.md', 'self_reflect_config.json', 'command_history.sh'])
def test_trajectory_gate_rejects_stale_pass(tmp_path, changed):
    agent, _, _ = passing_gate(tmp_path)
    (tmp_path / changed).write_text('changed')
    assert agent._self_reflection_gate_error().startswith('Completion blocked:')


def test_trajectory_gate_rejects_incomplete_image_records(tmp_path):
    agent, path, result = passing_gate(tmp_path)
    result['image_records'] = []
    path.write_text(json.dumps(result))
    assert 'different lengths' in agent._self_reflection_gate_error()
