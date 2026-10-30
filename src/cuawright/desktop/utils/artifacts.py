import hashlib
import json
import os
import stat
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from ..exceptions import ReleaseError


def digest(data):
    return hashlib.sha256(data).hexdigest()


def file_hash(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ReleaseError("provenance requires nonsymlink regular files")
    sha = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            sha.update(block)
    return sha.hexdigest()


def inventory(root):
    entries = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ReleaseError("provenance directory contains symlink")
        if path.is_file():
            entries.append([path.relative_to(root).as_posix(), file_hash(path)])
        elif not path.is_dir():
            raise ReleaseError("provenance directory contains special file")
    return {
        "files": len(entries),
        "sha256": digest(json.dumps(entries, separators=(",", ":")).encode()),
    }


def runtime_inventory(root):
    entries = [
        [path.relative_to(root).as_posix(), file_hash(path)]
        for path in sorted(root.rglob("*.py"))
    ]
    return {
        "files": len(entries),
        "sha256": digest(json.dumps(entries, separators=(",", ":")).encode()),
    }


def git(root, *args):
    result = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=False
    )
    if result.returncode:
        raise ReleaseError("required Git inspection failed")
    return result.stdout.strip()


def harness_provenance():
    root = Path(__file__).resolve().parents[4]
    if (root / ".git").exists():
        return {
            "commit": git(root, "rev-parse", "HEAD"),
            "tree": git(root, "rev-parse", "HEAD^{tree}"),
            "dirty": bool(git(root, "status", "--porcelain", "--untracked-files=all")),
            "status_sha256": digest(
                git(root, "status", "--porcelain", "--untracked-files=all").encode()
            ),
            "diff_sha256": digest(git(root, "diff", "HEAD", "--binary").encode()),
            "runtime": runtime_inventory(Path(__file__).parents[1]),
        }
    metadata = Path(__file__).parents[1] / "build_provenance.json"
    if not metadata.is_file():
        raise ReleaseError("wheel build provenance is missing")
    provenance = json.loads(metadata.read_text(encoding="utf-8"))
    if provenance["runtime"] != runtime_inventory(Path(__file__).parents[1]):
        raise ReleaseError("wheel runtime does not match build provenance")
    return provenance


def credentials(path):
    path = Path(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "r", encoding="utf-8") as stream:
        info = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_mode & 0o077
            or info.st_size > 8192
            or info.st_uid != os.getuid()
        ):
            raise ReleaseError("credential file must be owned, regular, and mode 0600")
        key = stream.read().strip()
    if not key or any(character.isspace() for character in key):
        raise ReleaseError("credential file must contain one nonempty API key")
    return key


class Store:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(mode=0o700, parents=False, exist_ok=False)
        self.trace = self.root / "trace.jsonl"
        self.sequence = 0

    def write(self, name, payload):
        path = self.root / name
        stage = self.root / (name + ".pending")
        data = (json.dumps(payload, indent=2, allow_nan=False) + "\n").encode()
        try:
            fd = os.open(stage, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(stage, path)
            directory = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except OSError:
            raise ReleaseError("result persistence failed") from None

    def event(self, kind, payload):
        self.sequence += 1
        record = {
            "sequence": self.sequence,
            "time": datetime.now(timezone.utc).isoformat(),
            "kind": kind,
            "data": payload,
        }
        try:
            fd = os.open(
                self.trace,
                os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW,
                0o600,
            )
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(json.dumps(record, allow_nan=False) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
        except OSError:
            raise ReleaseError("trace persistence failed") from None
