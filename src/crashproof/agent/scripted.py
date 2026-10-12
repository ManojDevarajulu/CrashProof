from typing import Literal

from crashproof.agent.base import Action, AgentError, Done, Observation, Wait
from crashproof.models import Order, ReturnRequest
from crashproof.runtime.tools import ToolCall

Stage = Literal[
    "get_order",
    "check_policy",
    "label",
    "early_refund",
    "await_scan",
    "refund",
    "close",
    "done",
]

_NEXT: dict[Stage, Stage] = {
    "get_order": "check_policy",
    "check_policy": "label",
    "label": "await_scan",
    "early_refund": "close",
    "refund": "close",
    "close": "done",
}


class ScriptedAgent:
    def __init__(
        self,
        request: ReturnRequest,
        *,
        max_retries: int = 3,
        early_refund: bool = False,
    ) -> None:
        self._request = request
        self._early_refund = early_refund
        self._awaiting_approval = False
        self._max_retries = max_retries
        self._stage: Stage = "get_order"
        self._started = False
        self._last_call = ToolCall(
            tool="get_order", args={"order_id": request.order_id}
        )
        self._retries = 0
        self._amount = 0

    def next_action(self, obs: Observation) -> Action:
        if self._stage == "done":
            return Done()
        if not self._started:
            return self._start(obs)
        if obs.last_call != self._last_call:
            raise AgentError(
                f"observation last_call {obs.last_call} != proposed {self._last_call}"
            )
        if self._awaiting_approval:
            return self._after_escalation(obs)
        if self._stage == "await_scan":
            return self._after_scan_check(obs)
        return self._after_call(obs)

    def _start(self, obs: Observation) -> Action:
        if obs.last_call is not None:
            raise AgentError("first observation must have last_call None")
        self._started = True
        return self._propose()

    def _after_call(self, obs: Observation) -> Action:
        if obs.timed_out:
            return self._retry(self._last_call)
        if obs.last_result is None:
            raise AgentError("observation has neither a result nor a timeout")
        if not obs.last_result.ok:
            return self._after_refusal(obs.last_result.error)
        self._retries = 0
        if self._stage == "get_order":
            order = Order.model_validate(obs.last_result.data["order"])
            self._amount = self._returned_amount(order)
            if self._amount == 0:
                return self._finish()
        if self._stage == "label" and self._early_refund and not obs.scan_received:
            self._stage = "early_refund"
            return self._propose()
        self._stage = _NEXT[self._stage]
        if self._stage == "await_scan":
            return self._after_scan_check(obs)
        if self._stage == "done":
            return Done()
        return self._propose()

    def _after_refusal(self, error: str | None) -> Action:
        if self._stage in ("early_refund", "refund"):
            if error == "policy_denied:P3":
                self._stage = "await_scan"
                return Wait()
            if error == "escalated":
                self._stage = "refund"
                self._awaiting_approval = True
                return Wait()
        return self._finish()

    def _after_escalation(self, obs: Observation) -> Action:
        if not obs.approval_decided:
            return Wait()
        self._awaiting_approval = False
        return self._propose()

    def _after_scan_check(self, obs: Observation) -> Action:
        if not obs.scan_received:
            return Wait()
        self._stage = "refund"
        return self._propose()

    def _retry(self, call: ToolCall) -> Action:
        if self._retries >= self._max_retries:
            return self._finish()
        self._retries += 1
        return call

    def _finish(self) -> Done:
        self._stage = "done"
        return Done()

    def _returned_amount(self, order: Order) -> int:
        return sum(
            line.qty * line.unit_price
            for line in order.lines
            if line.sku in self._request.line_skus
        )

    def _propose(self) -> ToolCall:
        order_id = self._request.order_id
        call: ToolCall
        if self._stage == "get_order":
            call = ToolCall(tool="get_order", args={"order_id": order_id})
        elif self._stage == "check_policy":
            call = ToolCall(tool="check_policy", args={"order_id": order_id})
        elif self._stage == "label":
            call = ToolCall(tool="create_return_label", args={"order_id": order_id})
        elif self._stage in ("early_refund", "refund"):
            call = ToolCall(
                tool="issue_refund",
                args={"order_id": order_id, "amount": self._amount},
            )
        else:
            call = ToolCall(tool="close_case", args={})
        self._last_call = call
        return call
