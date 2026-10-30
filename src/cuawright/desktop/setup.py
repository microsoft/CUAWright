import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

from .exceptions import ReleaseError

RELEASE_PATH = Path(__file__).with_name("osworld_release.json")
HF_ACCESS_HELP = (
    "Hugging Face access is required. Accept access to "
    "https://huggingface.co/datasets/xlangai/osworld_v2_tasks and "
    "https://huggingface.co/datasets/xlangai/osworld_v2_assets_gated, then run "
    "`uvx --from huggingface_hub hf auth login` and retry."
)


def release():
    return json.loads(RELEASE_PATH.read_text(encoding="utf-8"))


def file_hash(path):
    sha = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            sha.update(block)
    return sha.hexdigest()


def run(command, cwd=None):
    result = subprocess.run(command, cwd=cwd, check=False)
    if result.returncode:
        raise ReleaseError(f"command failed: {command[0]}")


def clone_source(path, metadata):
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    run(["git", "clone", metadata["source"]["repository"], str(path)])
    run(["git", "checkout", "--detach", metadata["source"]["commit"]], cwd=path)


def install_source(path):
    run([sys.executable, "-m", "pip", "install", "-e", str(path)])


def download_data(source, tasks, assets, metadata):
    run(
        [
            sys.executable,
            str(source / "scripts/tools/download_osworld_v2_tasks.py"),
            "--benchmark-release",
            metadata["release"],
            "--target-dir",
            str(tasks),
        ]
    )
    run(
        [
            sys.executable,
            str(source / "scripts/tools/download_osworld_v2_assets.py"),
            "--benchmark-release",
            metadata["release"],
            "--target-dir",
            str(assets),
            "--clean",
        ]
    )


def download_manifest(root, metadata):
    from huggingface_hub import hf_hub_download

    tasks = metadata["tasks"]
    try:
        downloaded = hf_hub_download(
            repo_id=tasks["repository"],
            repo_type=tasks["repo_type"],
            revision=tasks["tag"],
            filename=tasks["manifest_path"],
        )
    except Exception as exc:
        if type(exc).__name__ in {
            "GatedRepoError",
            "HfHubHTTPError",
            "RepositoryNotFoundError",
        }:
            raise ReleaseError(HF_ACCESS_HELP) from None
        raise
    target = root / "task_hashes.json"
    shutil.copy2(downloaded, target)
    return target


def check_asset_access(metadata):
    from huggingface_hub import HfApi

    assets = metadata["assets"]
    try:
        HfApi().list_repo_files(
            repo_id=assets["repository"],
            repo_type=assets["repo_type"],
            revision=assets["tag"],
        )
    except Exception as exc:
        if type(exc).__name__ in {
            "GatedRepoError",
            "HfHubHTTPError",
            "RepositoryNotFoundError",
        }:
            raise ReleaseError(HF_ACCESS_HELP) from None
        raise


def download_vm(root, metadata):
    from huggingface_hub import hf_hub_download

    vm = metadata["vm"]
    archive = Path(
        hf_hub_download(
            repo_id=vm["repository"],
            repo_type=vm["repo_type"],
            revision=vm["commit"],
            filename=vm["artifact"],
        )
    )
    if (
        archive.stat().st_size != vm["artifact_size"]
        or file_hash(archive) != vm["artifact_sha256"]
    ):
        raise ReleaseError("downloaded OSWorld VM artifact does not match release")
    target = root / "vm" / "Ubuntu.qcow2"
    target.parent.mkdir(parents=True, exist_ok=True)
    stage = target.with_suffix(".qcow2.pending")
    with zipfile.ZipFile(archive) as bundle:
        members = [
            item
            for item in bundle.infolist()
            if not item.is_dir() and item.filename.endswith(".qcow2")
        ]
        if len(members) != 1:
            raise ReleaseError("official VM archive must contain one qcow2 image")
        with bundle.open(members[0]) as source, stage.open("wb") as output:
            shutil.copyfileobj(source, output, 1024 * 1024)
    stage.replace(target)
    return target


def write_setup(root, paths, metadata):
    payload = {
        "release": metadata["release"],
        "source": str(paths["source"].resolve()),
        "tasks": str(paths["tasks"].resolve()),
        "assets": str(paths["assets"].resolve()),
        "task_manifest": str(paths["task_manifest"].resolve()),
        "vm": str(paths["vm"].resolve()),
        "vm_sha256": file_hash(paths["vm"]),
        "website_host_suffix": metadata["website"]["host_suffix"],
    }
    target = root / "setup.json"
    target.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return target


def verify_assets(path, metadata):
    expected = metadata["assets"]
    files = [
        item
        for item in path.rglob("*")
        if item.is_file() and ".cache" not in item.relative_to(path).parts
    ]
    metadata_files = sorted((path / ".cache/huggingface/download").rglob("*.metadata"))
    if len(files) != expected["file_count"] or len(metadata_files) != len(files):
        raise ReleaseError("official asset snapshot is incomplete")
    for item in metadata_files:
        if item.read_text(encoding="utf-8").splitlines()[0] != expected["commit"]:
            raise ReleaseError("asset snapshot does not match release lock")


def verify(config_path):
    metadata = release()
    config = json.loads(Path(config_path).read_text(encoding="utf-8"))
    if config.get("release") != metadata["release"]:
        raise ReleaseError("setup release does not match CUAWright release lock")
    paths = {name: Path(config[name]) for name in ("source", "tasks", "assets", "vm")}
    if (
        subprocess.run(
            ["git", "-C", str(paths["source"]), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
        ).stdout.strip()
        != metadata["source"]["commit"]
    ):
        raise ReleaseError("OSWorld source commit does not match release lock")
    status = subprocess.run(
        ["git", "-C", str(paths["source"]), "status", "--porcelain"],
        capture_output=True,
        text=True,
        check=False,
    )
    if status.returncode or status.stdout:
        raise ReleaseError("OSWorld source must be a clean Git checkout")
    manifest = Path(config["task_manifest"])
    if file_hash(manifest) != metadata["tasks"]["manifest_sha256"]:
        raise ReleaseError("task hash manifest does not match release lock")
    expected = json.loads(manifest.read_text(encoding="utf-8"))["files"]
    actual = sorted(paths["tasks"].glob("task_*.py"))
    if len(actual) != metadata["tasks"]["count"] or set(expected) != {
        path.name for path in actual
    }:
        raise ReleaseError("official task set is incomplete")
    for path in actual:
        item = expected[path.name]
        if path.stat().st_size != item["size"] or file_hash(path) != item["sha256"]:
            raise ReleaseError(f"official task file does not match: {path.name}")
    if not paths["assets"].is_dir():
        raise ReleaseError("OSWorld assets directory is missing")
    verify_assets(paths["assets"], metadata)
    if not paths["vm"].is_file() or paths["vm"].is_symlink():
        raise ReleaseError("OSWorld VM must be a nonsymlink file")
    if file_hash(paths["vm"]) != config.get("vm_sha256"):
        raise ReleaseError("selected OSWorld VM changed after setup")
    return config


def setup(args):
    metadata = release()
    root = args.root.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    paths = {
        "source": (args.source or root / "source").resolve(),
        "tasks": (args.tasks or root / "tasks").resolve(),
        "assets": (args.assets or root / "assets").resolve(),
        "vm": args.vm.resolve() if args.vm else None,
        "task_manifest": (args.task_manifest.resolve() if args.task_manifest else None),
    }
    if args.dry_run:
        print(json.dumps({key: str(value) for key, value in paths.items()}, indent=2))
        return 0
    clone_source(paths["source"], metadata)
    if not args.skip_install:
        install_source(paths["source"])
    if paths["task_manifest"] is None:
        paths["task_manifest"] = download_manifest(root, metadata)
    if args.tasks is None or args.assets is None:
        if args.assets is None:
            check_asset_access(metadata)
        download_data(paths["source"], paths["tasks"], paths["assets"], metadata)
    if paths["vm"] is None:
        paths["vm"] = download_vm(root, metadata)
    target = write_setup(root, paths, metadata)
    verify(target)
    print(target)
    return 0


def parser():
    result = argparse.ArgumentParser(description="Prepare or verify pinned OSWorld-V2.")
    commands = result.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("setup")
    prepare.add_argument("--root", required=True, type=Path)
    for name in ("source", "tasks", "assets", "vm", "task-manifest"):
        prepare.add_argument("--" + name, type=Path)
    prepare.add_argument("--skip-install", action="store_true")
    prepare.add_argument("--dry-run", action="store_true")
    check = commands.add_parser("verify")
    check.add_argument("--setup", required=True, type=Path)
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    if args.command == "setup":
        return setup(args)
    verify(args.setup)
    print("OSWorld setup verified.")
    return 0
