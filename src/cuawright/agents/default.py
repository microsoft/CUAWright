"""Shared scheduling and model-call accounting for browser and desktop agents.

Adapters own their message formats, completion gates, and compaction policy.
A step returns False when maintenance must wait (e.g. truncated tool calls).
"""

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass
class CallBudget:
    limit: int | None
    used: int = 0

    @property
    def remaining(self) -> int | None:
        return None if self.limit is None else max(0, self.limit - self.used)

    @property
    def exhausted(self) -> bool:
        return self.remaining == 0

    def charge(self) -> None:
        self.used += 1


class LoopAdapter(Protocol):
    def loop_ready(self) -> bool: ...
    def loop_step(self) -> bool: ...
    def loop_done(self) -> bool: ...
    def loop_maintain(self) -> None: ...
    def loop_result(self) -> dict[str, Any]: ...


def run_loop(adapter: LoopAdapter) -> dict[str, Any]:
    while adapter.loop_ready():
        maintain = adapter.loop_step()
        if adapter.loop_done():
            break
        if maintain:
            adapter.loop_maintain()
    return adapter.loop_result()
