import os
import re
import shlex
import sys
import tempfile
import time
from functools import wraps
from pathlib import Path


GUI = {
    "blender",
    "eog",
    "evince",
    "firefox",
    "gimp",
    "google-chrome",
    "libreoffice",
    "shotcut",
    "thunderbird",
    "vlc",
    "wps",
    "wpp",
    "et",
}


def install():
    patch_setup()
    patch_ports()


def patch_task(task):
    if str(task.get("id")) != "075":
        return None
    module = sys.modules[type(task).__module__]
    original = module.generate_text
    if getattr(original, "_cuawright_bounded_images", False):
        return 4096

    @wraps(original)
    def generate_text(*args, **kwargs):
        paths = kwargs.get("image_paths")
        if not paths:
            return original(*args, **kwargs)
        from PIL import Image

        Image.MAX_IMAGE_PIXELS = None
        with tempfile.TemporaryDirectory(prefix="cuawright-eval-images-") as directory:
            bounded = []
            for index, value in enumerate(paths):
                path = Path(value)
                with Image.open(path) as image:
                    if max(image.size) <= 4096:
                        bounded.append(str(path))
                        continue
                    image.thumbnail((4096, 4096), Image.Resampling.LANCZOS)
                    output = Path(directory) / f"{index}.png"
                    image.save(output, "PNG")
                    bounded.append(str(output))
            kwargs["image_paths"] = bounded
            return original(*args, **kwargs)

    generate_text._cuawright_bounded_images = True
    module.generate_text = generate_text
    return 4096


def patch_ports():
    from docker.errors import DockerException
    from desktop_env.providers.docker.provider import DockerProvider

    if getattr(DockerProvider, "_cuawright_dynamic_ports", False):
        return

    def clear(self):
        self.container = self.server_port = self.vnc_port = None
        self.chromium_port = self.vlc_port = None

    def published(self, port):
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            self.container.reload()
            value = self.container.attrs["NetworkSettings"]["Ports"].get(f"{port}/tcp")
            if value:
                return int(value[0]["HostPort"])
            time.sleep(0.1)
        raise TimeoutError(f"Docker did not publish port {port}")

    def start(self, path_to_vm, headless, os_type):
        del headless, os_type
        try:
            devices = ["/dev/kvm"] if os.path.exists("/dev/kvm") else []
            if not devices:
                self.environment["KVM"] = "N"
            self.container = self.client.containers.run(
                "happysixd/osworld-docker",
                environment=self.environment,
                cap_add=["NET_ADMIN"],
                devices=devices,
                volumes={
                    os.path.abspath(path_to_vm): {
                        "bind": "/System.qcow2",
                        "mode": "ro",
                    }
                },
                ports={8006: None, 5000: None, 9222: None, 8080: None},
                detach=True,
            )
            self.vnc_port, self.server_port, self.chromium_port, self.vlc_port = (
                published(self, port) for port in (8006, 5000, 9222, 8080)
            )
            self._wait_for_vm_ready()
        except (DockerException, OSError, RuntimeError, TimeoutError):
            if self.container is not None:
                try:
                    self.container.remove(force=True, v=True)
                except DockerException:
                    pass
                clear(self)
            raise

    DockerProvider._clear_container_state = clear
    DockerProvider.start_emulator = start
    DockerProvider._cuawright_dynamic_ports = True


def background(script):
    lexer = shlex.shlex(script, posix=True, punctuation_chars=";&|()<>")
    lexer.whitespace_split = True
    lexer.commenters = ""
    try:
        tokens = list(lexer)
    except ValueError:
        return False
    return (
        script == "cd /home/user/ds2019-request/ds2019-request && npm start"
        or "nohup" in tokens
        or "&" in tokens
    )


def patch_setup():
    from desktop_env.controllers.setup import SetupController

    if not getattr(SetupController, "_cuawright_execute", False):
        original = SetupController.execute

        @wraps(original)
        def execute(
            self,
            command,
            stdout="",
            stderr="",
            shell=False,
            until=None,
            quiet=False,
            timeout=120,
        ):
            if (
                isinstance(command, list)
                and command
                and not shell
                and not stdout
                and not stderr
                and not until
            ):
                executable = Path(command[0]).name
                args = set(command[1:])
                if executable in GUI and not (
                    args & {"--headless", "--convert-to", "--version"}
                ):
                    return self.launch(command)
                if executable in {"bash", "sh"} and "-c" in command:
                    script = command[command.index("-c") + 1]
                    if background(script):
                        result = self.launch(command)
                        match = re.search(r"\bsleep\s+(\d+(?:\.\d+)?)", script)
                        if match:
                            time.sleep(min(float(match.group(1)), 10))
                        return result
            return original(
                self,
                command,
                stdout=stdout,
                stderr=stderr,
                shell=shell,
                until=until,
                quiet=quiet,
                timeout=timeout,
            )

        SetupController.execute = execute
        SetupController._cuawright_execute = True

    if not getattr(SetupController, "_cuawright_proxy_bypass", False):
        original_launch = SetupController._launch_setup

        @wraps(original_launch)
        def launch(self, command, shell=False):
            if (
                isinstance(command, list)
                and command
                and Path(command[0]).name == "google-chrome"
                and self.use_proxy
            ):
                command = list(command)
                suffix = os.environ.get("WEBSITE_HOST_SUFFIX", "").strip()
                if suffix and not any(
                    str(value).startswith("--proxy-bypass-list=") for value in command
                ):
                    command.append(
                        f"--proxy-bypass-list=localhost;127.0.0.1;*.{suffix}"
                    )
            return original_launch(self, command, shell=shell)

        SetupController._launch_setup = launch
        SetupController._cuawright_proxy_bypass = True
