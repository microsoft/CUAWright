"""Native shell tool contract and replayable Responses output."""
import json
from typing import Any

DEFAULT_TOOL_FORMAT_ERROR_TEMPLATE = """Format error:

{{ error }}

Each turn must be either exactly one `run_command` tool call carrying a single shell
command, or a plain text message declaring the task complete. Never both, never neither.
"""

# Sole model-visible tool. The command is the whole turn: there is no thought,
# done, or final_response field, because completion is declared by answering with
# a plain text message instead of calling the tool.
RUN_COMMAND_TOOL: dict[str, Any] = {
    "type": "function",
    "name": "run_command",
    "description": (
        "Execute exactly one shell command in the task workspace and return its "
        "standard output, standard error, and exit status."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "One complete shell command. Heredocs are allowed.",
            }
        },
        "required": ["command"],
        "additionalProperties": False,
    },
    "strict": True,
}
TOOL_RESPONSE_MODE = "run_command_tool"



def _normalize_response_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Keep the output items that must be replayed verbatim on the next turn.

    Reasoning items are the model's carried-over memory: replaying them with their
    ``encrypted_content`` lets the next call resume from the same hidden state
    instead of re-deriving it across stateless Responses requests.
    """
    items: list[dict[str, Any]] = []
    for item in payload.get("output") or []:
        if not isinstance(item, dict):
            continue
        item_type = item.get("type")
        if item_type == "reasoning":
            reasoning: dict[str, Any] = {"type": "reasoning", "summary": item.get("summary") or []}
            if item.get("id"):
                reasoning["id"] = item["id"]
            if item.get("encrypted_content"):
                reasoning["encrypted_content"] = item["encrypted_content"]
            items.append(reasoning)
        elif item_type == "function_call":
            if not isinstance(item.get("call_id"), str) or not isinstance(item.get("name"), str):
                continue
            items.append(
                {
                    "type": "function_call",
                    "call_id": item["call_id"],
                    "name": item["name"],
                    "arguments": item.get("arguments", "{}"),
                }
            )
        elif item_type == "message":
            text = "\n".join(
                content.get("text", "")
                for content in item.get("content") or []
                if isinstance(content, dict) and content.get("type") == "output_text"
            )
            if text:
                items.append(
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": text}],
                    }
                )
    return items

def parse_tool_call_output(payload: dict[str, Any]) -> dict[str, Any]:
    """Map a Responses tool-calling turn onto the harness action contract.

    One ``run_command`` call means "run this and continue"; a plain text message
    with no call means "the task is finished, here is the answer".
    """
    call: dict[str, Any] | None = None
    text_parts: list[str] = []
    for item in payload.get("output") or []:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "function_call" and call is None:
            call = item
        elif item.get("type") == "message":
            text_parts.extend(
                content.get("text", "")
                for content in item.get("content") or []
                if isinstance(content, dict) and content.get("type") == "output_text"
            )
    text = "\n".join(part for part in text_parts if part.strip()).strip()

    if call is None:
        if not text:
            raise ValueError(
                "Model returned neither a run_command tool call nor any text. Call "
                "run_command with one shell command, or answer with the final response."
            )
        return {"thought": "", "bash_command": "", "python_code": "", "done": True, "final_response": text}

    if call.get("name") != "run_command":
        raise ValueError(f"Unknown tool {call.get('name')!r}; only run_command is available.")
    try:
        arguments = json.loads(call.get("arguments") or "{}")
    except json.JSONDecodeError as exc:
        raise ValueError(f"run_command arguments are not valid JSON: {exc}") from exc
    if not isinstance(arguments, dict):
        raise ValueError("run_command arguments must be a JSON object.")
    command = str(arguments.get("command", "") or "").strip()
    if not command:
        raise ValueError(
            "run_command was called with an empty command. Send one shell command, or "
            "declare completion with a plain text message instead of calling the tool."
        )
    return {"thought": text, "bash_command": command, "python_code": "", "done": False, "final_response": ""}
