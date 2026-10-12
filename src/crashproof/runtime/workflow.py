from typing import Literal

from pydantic import ValidationError

from crashproof.agent.base import Agent, Done, Observation, Wait
from crashproof.clock import Clock, Scheduler
from crashproof.config import PolicyConfig
from crashproof.models import Actor, Json, ReturnRequest
from crashproof.runtime.executor import EffectResult, Executor, Reconciler
from crashproof.runtime.ledger import Ledger, LedgerError, WorkflowRow
from crashproof.runtime.policy import (
    ApprovalLog,
    PolicyEngine,
    SimulatedApprover,
)
from crashproof.runtime.tools import (
    CheckPolicyArgs,
    CreateReturnLabelArgs,
    EscalateToHumanArgs,
    GetOrderArgs,
    IssueRefundArgs,
    ToolArgs,
    ToolCall,
    ToolResult,
    validate_args,
)
from crashproof.sim.trace import TraceRecorder
from crashproof.world.carriers import LabelProvider
from crashproof.world.events import EventBus, WorldEvent
from crashproof.world.faults import FaultInjector
from crashproof.world.refunds import RefundService
from crashproof.world.store import OrderNotFound, Store

type WorkflowState = Literal[
    "RECEIVED",
    "ELIGIBILITY_CHECKED",
    "LABEL_PENDING",
    "LABEL_CREATED",
    "AWAITING_RETURN_SCAN",
    "REFUND_PENDING",
    "REFUND_CONFIRMED",
    "CLOSED",
    "DENIED",
    "ESCALATED",
    "FAILED_NEEDS_REVIEW",
]

MAIN: tuple[WorkflowState, ...] = (
    "RECEIVED",
    "ELIGIBILITY_CHECKED",
    "LABEL_PENDING",
    "LABEL_CREATED",
    "AWAITING_RETURN_SCAN",
    "REFUND_PENDING",
    "REFUND_CONFIRMED",
    "CLOSED",
)
TERMINAL: frozenset[str] = frozenset({"CLOSED", "FAILED_NEEDS_REVIEW", "DENIED"})


class WorkflowError(Exception):
    """Raised on an illegal state transition."""


class _EffectTimedOut(Exception):
    """An effect timed out; the agent sees a timed_out observation."""


_REFUND_PENDING: WorkflowState = "REFUND_PENDING"


class Workflow:
    """The workflow state machine. Every change is written to the ledger."""

    def __init__(
        self, ledger: Ledger, workflow_id: str, trace: TraceRecorder, clock: Clock
    ) -> None:
        self._ledger = ledger
        self._workflow_id = workflow_id
        self._trace = trace
        self._clock = clock
        if ledger.get_workflow(workflow_id) is None:
            ledger.upsert_workflow(workflow_id, "RECEIVED", None)

    @property
    def state(self) -> str:
        return self._row().state

    @property
    def outcome(self) -> str | None:
        return self._row().outcome

    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL

    def advance_to(self, target: WorkflowState) -> None:
        current = self.state
        if target not in MAIN or (current not in MAIN and current != "ESCALATED"):
            raise WorkflowError(f"cannot advance from {current} to {target}")
        here, there = (
            MAIN.index(_REFUND_PENDING if current == "ESCALATED" else current),
            MAIN.index(target),
        )
        if current == "ESCALATED" and there > here:
            raise WorkflowError(f"cannot advance from ESCALATED to {target}")
        if there <= here:
            self._emit("workflow.replay", {"state": current, "target": target})
        elif there == here + 1:
            outcome = "closed" if target == "CLOSED" else None
            self._ledger.upsert_workflow(self._workflow_id, target, outcome)
            self._emit("workflow.advance", {"from": current, "to": target})
        else:
            raise WorkflowError(f"cannot skip from {current} to {target}")

    def escalate(self) -> None:
        self._move(("REFUND_PENDING",), "ESCALATED", None)

    def resume_from_escalation(self) -> None:
        self._move(("ESCALATED",), "REFUND_PENDING", None)

    def back_to_awaiting(self) -> None:
        self._move(("REFUND_PENDING",), "AWAITING_RETURN_SCAN", None)

    def deny(self, rule_id: str) -> None:
        outcome = f"denied_{rule_id}"
        self._move(("LABEL_PENDING", "REFUND_PENDING", "ESCALATED"), "DENIED", outcome)

    def _move(
        self,
        sources: tuple[WorkflowState, ...],
        target: WorkflowState,
        outcome: str | None,
    ) -> None:
        current = self.state
        if current not in sources:
            raise WorkflowError(f"illegal transition {current} -> {target}")
        self._ledger.upsert_workflow(self._workflow_id, target, outcome)
        data: dict[str, Json] = {"from": current, "to": target}
        if outcome is not None:
            data["outcome"] = outcome
        self._emit("workflow.advance", data)

    def fail(self, outcome: str) -> None:
        current = self.state
        if current in TERMINAL:
            raise WorkflowError(f"workflow already terminal in {current}")
        self._ledger.upsert_workflow(self._workflow_id, "FAILED_NEEDS_REVIEW", outcome)
        self._emit(
            "workflow.advance",
            {"from": current, "to": "FAILED_NEEDS_REVIEW", "outcome": outcome},
        )

    def _row(self) -> WorkflowRow:
        row = self._ledger.get_workflow(self._workflow_id)
        if row is None:
            raise LedgerError(f"no workflow row for {self._workflow_id!r}")
        return row

    def _emit(self, event: str, data: dict[str, Json]) -> None:
        self._trace.record(self._clock.now, self._workflow_id, "runtime", event, data)


class HardenedRuntime:
    """Drives the agent through keys, ledger, executor and the state machine.

    Same protocol as NaiveRuntime. A SimulatedCrash is never caught here; the
    runner models the restart by rebuilding this object on the same ledger.
    """

    def __init__(
        self,
        request: ReturnRequest,
        agent: Agent,
        *,
        store: Store,
        carrier: LabelProvider,
        refunds: RefundService,
        bus: EventBus,
        trace: TraceRecorder,
        clock: Clock,
        ledger: Ledger,
        max_attempts: int,
        faults: FaultInjector | None = None,
        reconciler: Reconciler | None = None,
        policy_config: PolicyConfig | None = None,
        approvals: ApprovalLog | None = None,
        approver: SimulatedApprover | None = None,
        scheduler: Scheduler | None = None,
        scan_timeout: int | None = None,
    ) -> None:
        policy_args = (policy_config, approvals, approver, scheduler, scan_timeout)
        given = [arg is not None for arg in policy_args]
        if any(given) and not all(given):
            raise ValueError(
                "policy_config, approvals, approver, scheduler and scan_timeout"
                " must be given together"
            )
        self._request = request
        self._agent = agent
        self._store = store
        self._bus = bus
        self._trace = trace
        self._clock = clock
        self._ledger = ledger
        self._reference = request.workflow_id
        self._workflow = Workflow(ledger, self._reference, trace, clock)
        policy = (
            None
            if policy_config is None or approvals is None
            else PolicyEngine(
                policy_config, store, request, ledger, approvals, trace, clock
            )
        )
        self._approvals = approvals
        self._approver = approver
        self._scheduler = scheduler
        self._scan_timeout = scan_timeout
        self._executor = Executor(
            ledger,
            carrier,
            refunds,
            faults,
            trace,
            clock,
            max_attempts,
            reconciler,
            policy=policy,
        )
        self._scan_received = False
        self._waiting = False
        self._waiting_approval = False
        self._last_call: ToolCall | None = None
        self._last_result: ToolResult | None = None

    @property
    def is_terminal(self) -> bool:
        return self._workflow.is_terminal

    @property
    def outcome(self) -> str | None:
        return self._workflow.outcome

    def start(self) -> None:
        self._ledger.recover()
        if self.is_terminal:
            return
        row = self._ledger.get_workflow(self._reference)
        self._scan_received = row is not None and row.scan_received
        self._bus.subscribe(self._on_event)
        self._schedule_scan_timeout()
        self._run(self._observation(timed_out=False))

    def on_approval(self, workflow_id: str) -> None:
        if workflow_id != self._reference or not self._waiting_approval:
            return
        if self.is_terminal:
            return
        self._waiting_approval = False
        self._run(self._observation(timed_out=False, approval_decided=True))

    def _schedule_scan_timeout(self) -> None:
        if self._scheduler is None or self._scan_timeout is None:
            return
        ledger, trace, clock = self._ledger, self._trace, self._clock
        reference = self._reference

        def expire() -> None:
            row = ledger.get_workflow(reference)
            if row is None or row.state in TERMINAL or row.scan_received:
                return
            Workflow(ledger, reference, trace, clock).fail("scan_timeout")
            data: dict[str, Json] = {"outcome": "scan_timeout"}
            trace.record(clock.now, reference, "runtime", "runtime.terminal", data)

        due = max(self._clock.now, self._request.requested_at + self._scan_timeout)
        self._scheduler.schedule_at(due, expire)

    def _on_event(self, event: WorldEvent) -> None:
        if self.is_terminal:
            return
        if event.type != "return.scanned" or event.workflow_id != self._reference:
            return
        if not self._scan_received:
            self._ledger.set_scan_received(self._reference)
            self._scan_received = True
        if self._waiting:
            self._waiting = False
            self._run(self._observation(timed_out=False))

    def _observation(
        self, *, timed_out: bool, approval_decided: bool = False
    ) -> Observation:
        return Observation(
            last_call=self._last_call,
            last_result=None if timed_out else self._last_result,
            timed_out=timed_out,
            scan_received=self._scan_received,
            approval_decided=approval_decided,
        )

    def _run(self, obs: Observation) -> None:
        while True:
            action = self._agent.next_action(obs)
            if isinstance(action, Wait):
                if self._workflow.state == "ESCALATED":
                    self._waiting_approval = True
                else:
                    self._workflow.advance_to("AWAITING_RETURN_SCAN")
                    self._waiting = True
                return
            if isinstance(action, Done):
                self._fail("gave_up")
                return
            obs = self._step(action)
            if self.is_terminal:
                return

    def _step(self, call: ToolCall) -> Observation:
        self._emit("agent", "agent.proposed", {"tool": call.tool})
        self._last_call = call
        try:
            args = validate_args(call)
        except ValidationError:
            self._emit("runtime", "runtime.invalid_call", {"tool": call.tool})
            self._fail("invalid_call")
            return self._observation(timed_out=False)
        try:
            result = self._execute(args)
        except _EffectTimedOut:
            self._emit("runtime", "runtime.tool_timeout", {"tool": call.tool})
            self._last_result = None
            return self._observation(timed_out=True)
        self._emit(
            "runtime", "runtime.tool_executed", {"tool": call.tool, "ok": result.ok}
        )
        self._last_result = result
        if call.tool == "close_case":
            self._workflow.advance_to("CLOSED")
            self._emit("runtime", "runtime.terminal", {"outcome": "closed"})
        return self._observation(timed_out=False)

    def _execute(self, args: ToolArgs) -> ToolResult:
        if isinstance(args, GetOrderArgs):
            return self._get_order(args.order_id)
        if isinstance(args, CheckPolicyArgs):
            return self._check_policy(args.order_id)
        if isinstance(args, CreateReturnLabelArgs):
            self._workflow.advance_to("LABEL_PENDING")
            outcome = self._executor.execute(
                self._reference, "create_label", args.order_id
            )
            return self._effect_result(outcome, "LABEL_CREATED")
        if isinstance(args, IssueRefundArgs):
            if self._workflow.state == "LABEL_CREATED":
                self._workflow.advance_to("AWAITING_RETURN_SCAN")
            self._workflow.advance_to("REFUND_PENDING")
            if self._workflow.state == "ESCALATED":
                if self._approval_status() in ("none", "pending"):
                    return ToolResult(ok=False, data={}, error="escalated")
                self._workflow.resume_from_escalation()
            outcome = self._executor.execute(
                self._reference, "issue_refund", args.order_id, args.amount
            )
            return self._effect_result(outcome, "REFUND_CONFIRMED", args.amount)
        if isinstance(args, EscalateToHumanArgs):
            return ToolResult(ok=False, data={}, error="not_supported_in_cut_1")
        return ToolResult(ok=True, data={})

    def _approval_status(self) -> str:
        if self._approvals is None:
            raise WorkflowError("workflow escalated without an approval log")
        return self._approvals.status(self._reference)

    def _effect_result(
        self,
        outcome: EffectResult,
        confirmed_state: WorkflowState,
        amount: int | None = None,
    ) -> ToolResult:
        if outcome.kind == "timed_out":
            raise _EffectTimedOut
        if outcome.kind == "exhausted":
            self._fail("needs_review")
            return ToolResult(ok=False, data={}, error="exhausted")
        if outcome.kind == "failed":
            return ToolResult(ok=False, data={}, error=outcome.error)
        if outcome.kind == "escalated":
            return self._escalate(amount)
        if outcome.kind == "denied":
            return self._denied(outcome)
        self._workflow.advance_to(confirmed_state)
        return ToolResult(ok=True, data=outcome.data)

    def _escalate(self, amount: int | None) -> ToolResult:
        if self._approver is None or amount is None:
            raise WorkflowError("escalation needs an approver and an amount")
        self._workflow.escalate()
        self._approver.request(self._reference, amount)
        return ToolResult(ok=False, data={}, error="escalated")

    def _denied(self, outcome: EffectResult) -> ToolResult:
        rule_id = outcome.data["rule_id"]
        if not isinstance(rule_id, str):
            raise WorkflowError("denied result without a rule id")
        if rule_id == "P3":
            self._workflow.back_to_awaiting()
        else:
            self._workflow.deny(rule_id)
            self._emit("runtime", "runtime.terminal", {"outcome": f"denied_{rule_id}"})
        return ToolResult(ok=False, data={}, error=outcome.error)

    def _get_order(self, order_id: str) -> ToolResult:
        try:
            order = self._store.get_order(order_id)
        except OrderNotFound:
            return ToolResult(ok=False, data={}, error="order_not_found")
        return ToolResult(ok=True, data={"order": order.model_dump(mode="json")})

    def _check_policy(self, order_id: str) -> ToolResult:
        try:
            order = self._store.get_order(order_id)
        except OrderNotFound:
            return ToolResult(ok=False, data={}, error="order_not_found")
        self._workflow.advance_to("ELIGIBILITY_CHECKED")
        data: dict[str, Json] = {
            "delivered_at": order.delivered_at,
            "fraud_risk": order.fraud_risk,
            "paid_amount": order.paid_amount,
        }
        return ToolResult(ok=True, data=data)

    def _fail(self, outcome: str) -> None:
        self._workflow.fail(outcome)
        self._emit("runtime", "runtime.terminal", {"outcome": outcome})

    def _emit(self, actor: Actor, event: str, data: dict[str, Json]) -> None:
        self._trace.record(self._clock.now, self._reference, actor, event, data)
