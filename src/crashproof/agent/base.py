from typing import Literal, Protocol

from pydantic import model_validator

from crashproof.runtime.tools import ToolCall, ToolModel, ToolResult


class Observation(ToolModel):
    last_call: ToolCall | None
    last_result: ToolResult | None
    timed_out: bool
    scan_received: bool
    approval_decided: bool = False

    @model_validator(mode="after")
    def _timeout_has_call_and_no_result(self) -> "Observation":
        if self.timed_out and self.last_call is None:
            raise ValueError("timed_out requires last_call")
        if self.timed_out and self.last_result is not None:
            raise ValueError("timed_out requires last_result to be None")
        return self


class Wait(ToolModel):
    kind: Literal["wait"] = "wait"


class Done(ToolModel):
    kind: Literal["done"] = "done"


Action = ToolCall | Wait | Done


class AgentError(Exception):
    pass


class Agent(Protocol):
    def next_action(self, obs: Observation) -> Action: ...
