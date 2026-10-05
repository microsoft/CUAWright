import base64
import hashlib
import json
import shlex
import urllib.error
import urllib.request
from pathlib import Path, PurePosixPath

from ...exceptions import ReleaseError
from ...tools.terminal import Result, exact_json

ROOT = "/opt/cuawright-tools"
CLIENTS = {
    name: (Path(__file__).parent / "control_clients" / f"{name}.py").read_bytes()
    for name in ("submit", "image_show", "ask_user")
}
CLIENT_HASHES = {
    name: hashlib.sha256(source).hexdigest() for name, source in CLIENTS.items()
}
IMAGE_LIMIT = 20 * 1024 * 1024
EXECUTION_LIMIT = 64 * 1024 * 1024

# These scripts are executable release code and included in the physical count.
INSTALL = """
import base64, json, os, stat
from pathlib import Path
payload = json.loads(base64.b64decode(PAYLOAD))
root = Path("/opt/cuawright-tools")
def secure_directory(path):
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise RuntimeError("insecure client directory")
secure_directory(root.parent)
if not root.exists():
    root.mkdir(mode=0o755)
secure_directory(root)
for name, encoded in payload.items():
    path = root / (name + ".py")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o644)
    with os.fdopen(fd, "wb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != 0:
            raise RuntimeError("insecure client file")
        os.fchmod(stream.fileno(), 0o644)
        stream.truncate(0)
        stream.write(base64.b64decode(encoded))
        stream.flush()
        os.fsync(stream.fileno())
print("installed")
"""

EXECUTE_CLIENT = """
import base64, hashlib, json, os, stat, sys
from pathlib import Path
payload = json.loads(base64.b64decode(PAYLOAD))
root = Path("/opt/cuawright-tools")
for path in (root.parent, root):
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise RuntimeError("insecure client directory")
path = root / (payload["name"] + ".py")
fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
with os.fdopen(fd, "rb") as stream:
    info = os.fstat(stream.fileno())
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise RuntimeError("insecure client file")
    source = stream.read(20000)
if hashlib.sha256(source).hexdigest() != payload["sha256"]:
    raise RuntimeError("client hash mismatch")
sys.argv = [str(path)] + payload["args"]
exec(compile(source, str(path), "exec"), {"__name__": "__main__"})
"""

READ_IMAGE = """
import base64, json, os, stat
payload = json.loads(base64.b64decode(PAYLOAD))
parts = payload["path"].split("/")[1:]
fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
try:
    for part in parts[:-1]:
        next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
        os.close(fd)
        fd = next_fd
    file_fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    with os.fdopen(file_fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > payload["limit"]:
            raise RuntimeError("not a bounded regular image")
        data = stream.read(payload["limit"] + 1)
        if len(data) > payload["limit"]:
            raise RuntimeError("image exceeded limit")
finally:
    os.close(fd)
print(base64.b64encode(data).decode("ascii"))
"""

SUPPORT = """
set -e
root="$HOME/.local/share/osw"
if [ ! -x "$root/venv/bin/python" ]; then python3 -m venv "$root/venv"; fi
if ! "$root/venv/bin/python" -c 'import playwright' >/dev/null 2>&1; then
  "$root/venv/bin/pip" install --quiet \
    --trusted-host pypi.org --trusted-host files.pythonhosted.org playwright
fi
if ! python3 -c 'import openpyxl, docx, pypdf, PIL, requests, bs4, lxml' >/dev/null 2>&1; then
  python3 -m pip install --user --quiet \
    --trusted-host pypi.org --trusted-host files.pythonhosted.org \
    openpyxl python-docx pypdf pillow requests beautifulsoup4 lxml
fi
"""

VERIFY = """
set -e
available=$(df -Pk / | awk 'NR==2 {print $4}')
[ "$available" -ge 5242880 ]
probe="$HOME/.cuawright-write-probe-$$"
: > "$probe"
rm -f "$probe"
printf '%s\\n' "$available"
"""


def materialize(script, payload):
    encoded = base64.b64encode(json.dumps(payload).encode()).decode()
    return script.replace("PAYLOAD", repr(encoded))


def control(command):
    """Only an exact standalone client invocation can request host dispatch."""
    if ROOT not in command:
        return None
    lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    lexer.commenters = ""
    try:
        words = list(lexer)
    except ValueError:
        raise ValueError("invalid control syntax") from None
    names = {f"{ROOT}/{name}.py": name for name in CLIENTS}
    if (
        command != command.strip()
        or len(words) < 2
        or words[0] != "python"
        or words[1] not in names
        or any(char in command for char in ("\n", "\r", "`", "$", "\\"))
        or any(
            word in (";", "|", "||", "&", "&&", ">", "<", "(", ")") for word in words
        )
    ):
        raise ValueError("control clients must be exact standalone commands")
    name = names[words[1]]
    args = words[2:]
    expected = {"cuawright_control": 1, "operation": name}
    if name == "submit":
        if args:
            raise ValueError("submit takes no arguments")
    elif name == "image_show":
        if len(args) != 2 or args[0] != "--path":
            raise ValueError("image_show requires --path")
        path = PurePosixPath(args[1])
        if (
            not path.is_absolute()
            or path.as_posix() != args[1]
            or args[1].startswith("//")
            or ".." in path.parts
            or args[1] == "/"
        ):
            raise ValueError("image path must be canonical and absolute")
        expected["path"] = args[1]
    else:
        if (
            len(args) != 2
            or args[0] != "--question"
            or not args[1].strip()
            or len(args[1]) > 2000
        ):
            raise ValueError("ask_user requires a nonempty question <=2000 characters")
        expected["question"] = args[1]
    return name, args, expected


class Guest:
    def __init__(self, env, post=None):
        self.env = env
        self.post = post or self._post
        self.submitted = False
        self.website_host_suffix = ""

    def _post(self, payload, timeout, limit):
        request = urllib.request.Request(
            self.env.controller.http_server + "/execute",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                data = response.read(limit + 1)
        except (urllib.error.URLError, OSError) as exc:
            return {
                "returncode": -1,
                "output": "",
                "error": f"Guest transport failed ({type(exc).__name__}).",
            }
        if len(data) > limit:
            return {
                "returncode": -1,
                "output": "",
                "error": "Guest response exceeded transport limit.",
            }
        try:
            return exact_json(data)
        except (ValueError, UnicodeError):
            return {
                "returncode": -1,
                "output": "",
                "error": "Guest returned invalid JSON.",
            }

    def execute(self, argv, timeout, limit=EXECUTION_LIMIT):
        self.env.is_environment_used = True
        payload = {
            "command": [
                "/usr/bin/timeout",
                "--verbose",
                "--signal=TERM",
                "--kill-after=5s",
                f"{timeout}s",
                *argv,
            ],
            "shell": False,
            "timeout": timeout + 15,
        }
        value = self.post(payload, timeout + 25, limit)
        if (
            not isinstance(value, dict)
            or type(value.get("returncode")) is not int
            or not isinstance(value.get("output"), str)
            or not isinstance(value.get("error"), str)
        ):
            return Result(-1, stderr="Invalid guest execution response.")
        return Result(value["returncode"], value["output"], value["error"])

    def python(self, script, payload, timeout=90, limit=2 * 1024 * 1024):
        return self.execute(
            ["/usr/bin/python3", "-c", materialize(script, payload)], timeout, limit
        )

    def provision_controls(self):
        previously_used = self.env.is_environment_used
        payload = {
            name: base64.b64encode(source).decode() for name, source in CLIENTS.items()
        }
        script = materialize(INSTALL, payload)
        password = getattr(self.env, "client_password", None)
        if password is None:
            argv = ["sudo", "-n", "/usr/bin/python3", "-c", script]
        else:
            if (
                not isinstance(password, str)
                or not password
                or any(char in password for char in "\n\r\0")
            ):
                raise ReleaseError("invalid guest administrative credential")
            command = (
                f"printf '%s\\n' {shlex.quote(password)} | "
                f"sudo -S -p '' /usr/bin/python3 -c {shlex.quote(script)}"
            )
            argv = ["/bin/bash", "-c", command]
        try:
            result = self.execute(argv, 90)
        finally:
            self.env.is_environment_used = previously_used
        if result.exit_code != 0 or result.stdout.strip() != "installed":
            raise ReleaseError("control client provisioning failed")

    def provision_support(self):
        support = self.execute(["/bin/bash", "-c", SUPPORT], 240)
        if support.exit_code:
            raise ReleaseError("guest support provisioning failed")
        verified = self.execute(["/bin/bash", "-c", VERIFY], 30)
        if verified.exit_code:
            raise ReleaseError("guest disk or writable-home verification failed")

    def provision(self):
        self.provision_controls()
        self.provision_support()

    def run(self, command, timeout):
        if self.submitted:
            raise ReleaseError("action after submission")
        try:
            request = control(command)
        except ValueError as exc:
            return Result(2, stderr=str(exc))
        if request is None:
            wrapped = (
                'export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:'
                '/sbin:/bin:$HOME/.local/bin"\n'
                f"export WEBSITE_HOST_SUFFIX={shlex.quote(self.website_host_suffix)}\n"
                + command
            )
            return self.execute(["/bin/bash", "-c", wrapped], timeout)
        name, args, expected = request
        executed = self.python(
            EXECUTE_CLIENT,
            {"name": name, "args": args, "sha256": CLIENT_HASHES[name]},
            timeout,
        )
        if executed.exit_code != 0:
            return Result(2, stderr="control client execution failed")
        if len(executed.stdout.encode("utf-8")) > 16_384:
            raise ReleaseError("control descriptor exceeded limit")
        try:
            descriptor = exact_json(executed.stdout)
        except ValueError:
            descriptor = None
        if (
            descriptor != expected
            or not isinstance(descriptor, dict)
            or type(descriptor.get("cuawright_control")) is not int
            or executed.stderr
        ):
            return Result(2, stderr="Control client descriptor verification failed.")
        if name == "submit":
            self.submitted = True
            return Result(stdout="Submitted.", completed=True)
        if name == "ask_user":
            simulator = self.env.user_simulator
            if simulator is None:
                return Result(2, stderr="No official user simulator configured.")
            try:
                answer = simulator.respond(expected["question"])
            except Exception:
                raise ReleaseError("official user simulator failed") from None
            if not isinstance(answer, str):
                raise ReleaseError("official user simulator returned invalid answer")
            return Result(stdout=answer)
        image = self.python(
            READ_IMAGE,
            {"path": expected["path"], "limit": IMAGE_LIMIT},
            timeout,
            limit=IMAGE_LIMIT * 2,
        )
        if image.exit_code:
            return Result(2, stderr="Unable to read a bounded nonsymlink image.")
        try:
            data = base64.b64decode(image.stdout.strip(), validate=True)
        except ValueError:
            return Result(2, stderr="Invalid image bridge response.")
        if len(data) > IMAGE_LIMIT:
            raise ReleaseError("image exceeded limit")
        if data.startswith(b"\x89PNG\r\n\x1a\n"):
            media = "image/png"
        elif data.startswith(b"\xff\xd8\xff"):
            media = "image/jpeg"
        else:
            return Result(2, stderr="Only PNG and JPEG images are supported.")
        return Result(
            stdout="Image attached.",
            image=data,
            media_type=media,
            path=expected["path"],
        )
