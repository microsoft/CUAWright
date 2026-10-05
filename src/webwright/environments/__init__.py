from __future__ import annotations

import copy
import importlib

from webwright import Environment

_ENVIRONMENT_MAPPING = {
    "local_browser": "webwright.environments.local_browser.LocalBrowserEnvironment",
    "local_workspace": "webwright.environments.local_workspace.LocalWorkspaceEnvironment",
}


def get_environment_class(spec: str) -> type[Environment]:
    full_path = _ENVIRONMENT_MAPPING.get(spec, spec)
    module_name, class_name = full_path.rsplit(".", 1)
    module = importlib.import_module(module_name)
    return getattr(module, class_name)


def get_environment(config: dict, *, default_type: str = "local_workspace") -> Environment:
    copied = copy.deepcopy(config)
    environment_class = copied.pop("environment_class", default_type)
    return get_environment_class(environment_class)(**copied)
