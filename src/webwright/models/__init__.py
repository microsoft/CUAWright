from __future__ import annotations

import copy
import importlib

from webwright import Model

_MODEL_MAPPING = {
    "openai": "webwright.models.openai_model.OpenAIModel",
    "anthropic": "webwright.models.anthropic_model.AnthropicModel",
    "openrouter": "webwright.models.openrouter_model.OpenRouterModel",
}


def get_model_class(spec: str) -> type[Model]:
    full_path = _MODEL_MAPPING.get(spec, spec)
    module_name, class_name = full_path.rsplit(".", 1)
    module = importlib.import_module(module_name)
    return getattr(module, class_name)


def get_model(config: dict, *, default_type: str = "openai") -> Model:
    copied = copy.deepcopy(config)
    model_class = copied.pop("model_class", default_type)
    return get_model_class(model_class)(**copied)
