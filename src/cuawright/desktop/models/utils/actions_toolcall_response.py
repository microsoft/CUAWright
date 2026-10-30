import json
from dataclasses import dataclass

OUTPUT_LIMIT = 16_000
TOOLS = [
    {
        "type": "function",
        "name": "run_command",
        "description": (
            "Execute a shell command in the active terminal and return its standard "
            "output, standard error, and exit status."
        ),
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "timeout": {"type": "integer", "minimum": 1, "maximum": 300},
            },
            "required": ["command", "timeout"],
            "additionalProperties": False,
        },
    }
]


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError("non-finite JSON constant")


def exact_json(text):
    return json.loads(text, object_pairs_hook=_unique, parse_constant=_invalid_constant)


def command_arguments(call):
    if call.get("name") != "run_command":
        raise ValueError("unknown tool")
    value = exact_json(call["arguments"])
    if not isinstance(value, dict) or set(value) != {"command", "timeout"}:
        raise ValueError("expected exactly command and timeout")
    command, timeout = value["command"], value["timeout"]
    if not isinstance(command, str) or not command.strip() or "\x00" in command:
        raise ValueError("command must be nonempty text without NUL")
    if type(timeout) is not int or not 1 <= timeout <= 300:
        raise ValueError("timeout must be an integer in 1..300")
    return command, timeout


def capped(text):
    if len(text) <= OUTPUT_LIMIT:
        return text
    return (
        text[:12_000]
        + "\n\n[OUTPUT TRUNCATED: request a narrower command, smaller range, "
        "or filtered output.]\n\n" + text[-3_000:]
    )


@dataclass
class Result:
    exit_code: int = 0
    stdout: str = ""
    stderr: str = ""
    completed: bool = False
    image: bytes | None = None
    media_type: str = ""
    path: str = ""

    def text(self):
        return capped(
            json.dumps(
                {
                    "exit_code": self.exit_code,
                    "stdout": self.stdout,
                    "stderr": self.stderr,
                    "completed": self.completed,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
