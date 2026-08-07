"""Tests for get_template_vars metric key naming."""

from webwright.models.base import BaseModel


class _ConcreteModel(BaseModel):
    """Minimal concrete subclass — no API key required."""

    _API_KEY_FIELD = ""


def test_last_usage_keys_do_not_overwrite_last_request_keys() -> None:
    model = _ConcreteModel(action_field="bash_command")

    # Inject distinct values so we can tell them apart.
    model._last_request_metrics["message_count"] = 3
    model._last_usage_metrics["input_tokens"] = 100

    result = model.get_template_vars()

    assert "last_request_message_count" in result, (
        "last_request_message_count missing from get_template_vars output"
    )
    assert "last_usage_input_tokens" in result, (
        "last_usage_input_tokens missing from get_template_vars output"
    )
    assert result["last_request_message_count"] == 3
    assert result["last_usage_input_tokens"] == 100

    # The old bug: usage keys were written as last_request_*, which would
    # clobber real request keys.  Verify the buggy key is absent.
    assert "last_request_input_tokens" not in result, (
        "last_request_input_tokens should not exist; usage metrics must use last_usage_ prefix"
    )


def test_cumulative_usage_keys_use_cumulative_usage_prefix() -> None:
    model = _ConcreteModel(action_field="bash_command")

    model._cumulative_usage_metrics["input_tokens"] = 500
    model._cumulative_request_metrics["message_count"] = 7

    result = model.get_template_vars()

    assert "cumulative_usage_input_tokens" in result
    assert result["cumulative_usage_input_tokens"] == 500

    assert "cumulative_request_message_count" in result
    assert result["cumulative_request_message_count"] == 7

    # Old bug: cumulative usage used "cumulative_" prefix, which is inconsistent.
    assert "cumulative_input_tokens" not in result, (
        "cumulative_input_tokens should not exist; cumulative usage must use cumulative_usage_ prefix"
    )
