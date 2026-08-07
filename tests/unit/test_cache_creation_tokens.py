from webwright.models.anthropic_model import _usage_from_anthropic_payload
from webwright.models.base import _USAGE_METRIC_KEYS


def test_cache_creation_input_tokens_in_metric_keys() -> None:
    assert "cache_creation_input_tokens" in _USAGE_METRIC_KEYS


def test_anthropic_payload_extracts_cache_creation_tokens() -> None:
    payload = {
        "usage": {
            "input_tokens": 100,
            "output_tokens": 50,
            "cache_read_input_tokens": 20,
            "cache_creation_input_tokens": 30,
        }
    }
    metrics = _usage_from_anthropic_payload(payload)
    assert metrics["cache_creation_input_tokens"] == 30
    assert metrics["cached_input_tokens"] == 20


def test_anthropic_payload_defaults_cache_creation_to_zero() -> None:
    payload = {"usage": {"input_tokens": 100, "output_tokens": 50}}
    metrics = _usage_from_anthropic_payload(payload)
    assert metrics["cache_creation_input_tokens"] == 0


def test_anthropic_payload_returns_all_metric_keys() -> None:
    payload = {"usage": {"input_tokens": 10, "output_tokens": 5}}
    metrics = _usage_from_anthropic_payload(payload)
    for key in _USAGE_METRIC_KEYS:
        assert key in metrics, f"missing key: {key}"
