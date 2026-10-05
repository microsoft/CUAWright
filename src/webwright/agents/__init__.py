from __future__ import annotations

import copy
import importlib

from webwright import Agent, Environment, Model

_AGENT_MAPPING = {
    "default": "webwright.agents.default.DefaultAgent",
}


def get_agent_class(spec: str) -> type[Agent]:
    full_path = _AGENT_MAPPING.get(spec, spec)
    module_name, class_name = full_path.rsplit(".", 1)
    module = importlib.import_module(module_name)
    return getattr(module, class_name)


def get_agent(model: Model, env: Environment, config: dict, *, default_type: str = "default") -> Agent:
    copied = copy.deepcopy(config)
    agent_class = get_agent_class(copied.pop("agent_class", default_type))
    return agent_class(model, env, **copied)
