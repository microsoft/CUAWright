from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Protocol

try:
    import dotenv
except ModuleNotFoundError:
    class _DotenvShim:
        @staticmethod
        def load_dotenv(*args, **kwargs):
            return False
    dotenv = _DotenvShim()
try:
    from platformdirs import user_config_dir
except ModuleNotFoundError:
    def user_config_dir(appname: str) -> str:
        return str(Path.home() / ".config" / appname)

__version__ = "0.1.0"

package_dir = Path(__file__).resolve().parent
global_config_dir = Path(
    os.getenv("WEBWRIGHT_CONFIG_DIR") or user_config_dir("webwright")
)
global_config_dir.mkdir(parents=True, exist_ok=True)
global_config_file = global_config_dir / ".env"
dotenv.load_dotenv(dotenv_path=global_config_file)


class Model(Protocol):
    config: Any

    def __call__(self, messages: list[dict[str, Any]], **kwargs) -> str: ...

    def query(self, messages: list[dict[str, Any]], **kwargs) -> dict[str, Any]: ...

    def format_message(self, **kwargs) -> dict[str, Any]: ...

    def format_observation_messages(
        self,
        message: dict[str, Any],
        outputs: list[dict[str, Any]],
        template_vars: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]: ...

    def get_template_vars(self, **kwargs) -> dict[str, Any]: ...

    def serialize(self) -> dict[str, Any]: ...


class Environment(Protocol):
    config: Any

    def prepare(self, **kwargs) -> None: ...

    def execute(self, action: dict[str, Any], cwd: str = "") -> dict[str, Any]: ...

    def get_template_vars(self, **kwargs) -> dict[str, Any]: ...

    def serialize(self) -> dict[str, Any]: ...

    def close(self) -> None: ...


class Agent(Protocol):
    config: Any

    def run(self, task: str, **kwargs) -> dict[str, Any]: ...

    def save(self, path: Path | None, *extra_dicts) -> dict[str, Any]: ...


__all__ = [
    "Agent",
    "Environment",
    "Model",
    "__version__",
    "global_config_dir",
    "global_config_file",
    "package_dir",
]
