"""Provider-neutral helpers for stateless OpenAI Responses tool sessions."""

import base64
import copy


def tool_session_options(tools):
    return {
        "tools": copy.deepcopy(tools),
        "parallel_tool_calls": False,
        "include": ["reasoning.encrypted_content"],
        "store": False,
    }


def image_part(data: bytes, media_type: str, *, detail: str = "auto"):
    return {
        "type": "input_image",
        "image_url": f"data:{media_type};base64,"
        + base64.b64encode(data).decode("ascii"),
        "detail": detail,
    }
