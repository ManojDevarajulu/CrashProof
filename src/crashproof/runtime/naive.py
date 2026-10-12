from pydantic import ValidationError

from crashproof.agent.base import Agent, Done, Observation, Wait
from crashproof.clock import Clock
from crashproof.models import Actor, Json, ReturnRequest
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
from crashproof.world.carriers import IdempotencyConflict, LabelProvider, LabelResult
from crashproof.world.events import EventBus, WorldEvent
from crashproof.world.faults import FaultInjector, Timeout
from crashproof.world.refunds import InvalidRefundAmount, RefundRecord, RefundService
from crashproof.world.store import OrderNotFound, Store


class NaiveRuntime:
    """Executes each agent proposal exactly once, straight against the world.

    No idempotency keys, no ledger, no policy, no runtime-level retry. A
    SimulatedCrash is never caught here; the runner models the restart.
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
        faults: FaultInjector | None = None,
    ) -> None:
        self._request = request
        self._agent = agent
        self._store = store
        self._carrier = carrier
        self._refunds = refunds
        self._bus = bus
        self._trace = trace
        self._clock = clock
        self._faults = faults
        self._reference = request.workflow_id
        self._scan_received = False
        self._waiting = False
        self._outcome: str | None = None
        self._last_call: ToolCall | None = None
        self._last_result: ToolResult | None = None

    @property
    def is_terminal(self) -> bool:
        return self._outcome is not None

    @property
    def outcome(self) -> str | None:
        return self._outcome

    def start(self) -> None:
        self._bus.subscribe(self._on_event)
        self._run(self._observation(timed_out=False))

    def _on_event(self, event: WorldEvent) -> None:
        if self.is_terminal:
            return
        if event.type != "return.scanned" or event.workflow_id != self._reference:
            return
        self._scan_received = True
        if self._waiting:
            self._waiting = False
            self._run(self._observation(timed_out=False))

    def _observation(self, *, timed_out: bool) -> Observation:
        return Observation(
            last_call=self._last_call,
            last_result=None if timed_out else self._last_result,
            timed_out=timed_out,
            scan_received=self._scan_received,
        )

    def _run(self, obs: Observation) -> None:
        while True:
            action = self._agent.next_action(obs)
            if isinstance(action, Wait):
                self._waiting = True
                return
            if isinstance(action, Done):
                self._finish("gave_up")
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
            self._finish("invalid_call")
            return self._observation(timed_out=False)
        try:
            result = self._execute(args)
        except Timeout:
            self._emit("runtime", "runtime.tool_timeout", {"tool": call.tool})
            self._last_result = None
            return self._observation(timed_out=True)
        self._emit(
            "runtime", "runtime.tool_executed", {"tool": call.tool, "ok": result.ok}
        )
        self._last_result = result
        if call.tool == "close_case":
            self._finish("closed")
        return self._observation(timed_out=False)

    def _execute(self, args: ToolArgs) -> ToolResult:
        if isinstance(args, GetOrderArgs):
            return self._get_order(args.order_id)
        if isinstance(args, CheckPolicyArgs):
            return self._check_policy(args.order_id)
        if isinstance(args, CreateReturnLabelArgs):
            return self._create_label(args.order_id)
        if isinstance(args, IssueRefundArgs):
            return self._issue_refund(args.order_id, args.amount)
        if isinstance(args, EscalateToHumanArgs):
            return ToolResult(ok=False, data={}, error="not_supported_in_cut_1")
        return ToolResult(ok=True, data={})

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
        data: dict[str, Json] = {
            "delivered_at": order.delivered_at,
            "fraud_risk": order.fraud_risk,
            "paid_amount": order.paid_amount,
        }
        return ToolResult(ok=True, data=data)

    def _call_create_label(self, order_id: str) -> LabelResult:
        return self._carrier.create_label(order_id, self._reference)

    def _call_issue_refund(self, order_id: str, amount: int) -> RefundRecord:
        return self._refunds.issue_refund(order_id, amount, self._reference)

    def _create_label(self, order_id: str) -> ToolResult:
        try:
            label = self._call_create_label(order_id)
        except IdempotencyConflict as exc:
            return ToolResult(ok=False, data={}, error=type(exc).__name__)
        if self._faults is not None:
            self._faults.crash_point("label_applied", self._reference)
        return ToolResult(
            ok=True, data={"label_id": label.label_id, "status": label.status}
        )

    def _issue_refund(self, order_id: str, amount: int) -> ToolResult:
        try:
            record = self._call_issue_refund(order_id, amount)
        except (IdempotencyConflict, InvalidRefundAmount) as exc:
            return ToolResult(ok=False, data={}, error=type(exc).__name__)
        if self._faults is not None:
            self._faults.crash_point("refund_applied", self._reference)
        return ToolResult(
            ok=True, data={"refund_id": record.refund_id, "amount": record.amount}
        )

    def _finish(self, outcome: str) -> None:
        self._outcome = outcome
        self._emit("runtime", "runtime.terminal", {"outcome": outcome})

    def _emit(self, actor: Actor, event: str, data: dict[str, Json]) -> None:
        self._trace.record(self._clock.now, self._reference, actor, event, data)
