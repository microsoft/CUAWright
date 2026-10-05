from __future__ import annotations


class InterruptAgentFlow(Exception):
    def __init__(self, *messages: dict):
        super().__init__()
        self.messages = list(messages)


class LimitsExceeded(InterruptAgentFlow):
    pass


class Submitted(InterruptAgentFlow):
    pass


class FormatError(InterruptAgentFlow):
    pass
