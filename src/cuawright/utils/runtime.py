from __future__ import annotations

import asyncio
from typing import TypeVar

T = TypeVar("T")


def run_async(coro) -> T:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    raise RuntimeError("Webwright does not support running inside an active event loop.")
