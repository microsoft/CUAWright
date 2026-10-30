import importlib
import hashlib
import json
import os
import re
import sys
from pathlib import Path

from ...exceptions import ReleaseError
from ...utils.artifacts import file_hash, git, inventory

RELEASE = "v2026.08.08"
COMMIT = "d578d2d4e0dc82b43e270fdaa7fa89d9708cd154"
REPOSITORY = "https://github.com/xlang-ai/OSWorld-V2.git"


def activate(root):
    root = Path(root).resolve(strict=True)
    if not (root / ".git").exists():
        raise ReleaseError("OSWorld source must be a Git checkout root")
    if git(root, "rev-parse", "HEAD") != COMMIT:
        raise ReleaseError("OSWorld source does not match supported official pin")
    if git(root, "status", "--porcelain", "--untracked-files=all"):
        raise ReleaseError("OSWorld source must be clean; no runtime patches allowed")
    for relative in ("desktop_env/desktop_env.py", "task_loader.py"):
        path = root / relative
        if not path.is_file() or path.is_symlink():
            raise ReleaseError("incomplete OSWorld source layout")
    for name, module in list(sys.modules.items()):
        if name.split(".")[0] in {"desktop_env", "task_loader"}:
            origin = getattr(module, "__file__", None)
            if origin is None or not Path(origin).resolve().is_relative_to(root):
                raise ReleaseError("OSWorld imported before source activation")
    sys.path.insert(0, str(root))
    importlib.invalidate_caches()
    return {
        "release": RELEASE,
        "repository": REPOSITORY,
        "commit": COMMIT,
        "tree": git(root, "rev-parse", "HEAD^{tree}"),
        "dirty": False,
    }


def load_task(tasks, task_id):
    if not re.fullmatch(r"[A-Za-z0-9_-]+", task_id):
        raise ReleaseError("invalid task identity")
    tasks = Path(tasks).resolve(strict=True)
    direct = tasks / f"task_{task_id}.py"
    loader = importlib.import_module("task_loader")
    path = (
        str(direct)
        if direct.is_file()
        else loader.find_task_class_path(task_id, str(tasks), "tasks", "v2")
    )
    if not path:
        raise ReleaseError("only official V2 Python class tasks are supported")
    path = Path(path).resolve(strict=True)
    if not path.is_relative_to(tasks):
        raise ReleaseError("task class resolves outside supplied tasks root")
    task_provenance = {
        "id": task_id,
        "class_sha256": file_hash(path),
        "source_tree": inventory(tasks),
    }
    task = loader.load_task_from_file(str(path))
    base = importlib.import_module("desktop_env.task_base").BaseTask
    if (
        not isinstance(task, base)
        or type(task).evaluate is base.evaluate
        or task.get("platform", "linux") != "linux"
        or str(task.get("id")) != task_id
    ):
        raise ReleaseError("require Linux V2 class task with custom evaluator")
    instruction = task.get("instruction")
    if not isinstance(instruction, str) or not instruction.strip():
        raise ReleaseError("task has no official instruction")
    sha = hashlib.sha256()
    try:
        encoder = json.JSONEncoder(
            sort_keys=True, separators=(",", ":"), allow_nan=False
        )
        for fragment in encoder.iterencode(dict(task)):
            sha.update(fragment.encode())
    except (TypeError, ValueError):
        raise ReleaseError(
            "official task configuration cannot be deterministically hashed"
        ) from None
    task_provenance["config_sha256"] = sha.hexdigest()
    return task, instruction, task_provenance


def configure_external(
    assets, key, responses_url, website_host_suffix, proxy_config=None
):
    from ....core.responses import sdk_base_url

    base_url = sdk_base_url(responses_url)
    values = {
        "WEBSITE_HOST_SUFFIX": website_host_suffix,
        "OPENAI_API_KEY": key,
        "OPENAI_BASE_URL": base_url,
        "OSWORLD_EVAL_MODEL_PROVIDER": "openai",
        "OSWORLD_EVAL_MODEL_API_KEY": key,
        "OSWORLD_EVAL_MODEL_BASE_URL": base_url,
        "OSWORLD_USER_SIM_API_KEY": key,
        "OSWORLD_USER_SIM_BASE_URL": base_url,
    }
    if assets is not None:
        values["OSWORLD_FILE_BASE_URL"] = str(assets)
    names = (*values, "PROXY_CONFIG_FILE")
    previous = {name: os.environ.get(name) for name in names}
    os.environ.update(values)
    if proxy_config is None:
        os.environ.pop("PROXY_CONFIG_FILE", None)
    else:
        os.environ["PROXY_CONFIG_FILE"] = str(proxy_config)
    return previous


def restore_external(previous):
    for name, value in previous.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value
