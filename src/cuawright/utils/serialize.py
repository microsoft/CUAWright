from __future__ import annotations

from typing import Any

UNSET = object()


def recursive_merge(*dictionaries: dict | None) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for dictionary in dictionaries:
        if dictionary is None:
            continue
        for key, value in dictionary.items():
            if value is UNSET:
                continue
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = recursive_merge(result[key], value)
            elif isinstance(value, dict):
                result[key] = recursive_merge(value)
            else:
                result[key] = value
    return result
