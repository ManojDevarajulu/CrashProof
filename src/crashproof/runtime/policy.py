from collections.abc import Callable
from typing import Literal

from pydantic import BaseModel, ConfigDict

from crashproof.clock import Clock, Scheduler
from crashproof.config import ApproverSettings, PolicyConfig, RuleId
from crashproof.models import Json, ReturnRequest
from crashproof.rng import Rng
from crashproof.runtime.ledger import EffectType, Ledger, LedgerError
from crashproof.sim.trace import TraceRecorder
from crashproof.world.store import Store

type VerdictKind = Literal["allow", "deny", "escalate"]
type ApprovalStatus = Literal["none", "pending", "approved", "denied"]

_DAY = 86400


class PolicyError(Exception):
    """Raised when a rule cannot be evaluated with the data it was given."""


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class PolicyVerdict(_Frozen):
    kind: VerdictKind
    rule_id: str
    decision_id: int


class ApprovalRecord(_Frozen):
    workflow_id: str
    amount: int
    status: Literal["pending", "approved", "denied"]


class ApprovalLog:
    """Human approvals, owned by the harness. The policy engine and I4 read it."""

    def __init__(self) -> None:
        self._records: dict[str, ApprovalRecord] = {}

    def request(self, workflow_id: str, amount: int) -> None:
        if workflow_id in self._records:
            return
        self._records[workflow_id] = ApprovalRecord(
            workflow_id=workflow_id, amount=amount, status="pending"
        )

    def record(self, workflow_id: str, approved: bool) -> None:
        pending = self._records.get(workflow_id)
        if pending is None or pending.status != "pending":
            raise PolicyError(f"no pending approval for {workflow_id!r}")
        self._records[workflow_id] = pending.model_copy(
            update={"status": "approved" if approved else "denied"}
        )

    def status(self, workflow_id: str) -> ApprovalStatus:
        found = self._records.get(workflow_id)
        return "none" if found is None else found.status

    def records(self) -> list[ApprovalRecord]:
        return [self._records[key] for key in sorted(self._records)]


class SimulatedApprover:
    """Deterministic human. Decides after a seeded virtual delay, then notifies."""

    def __init__(
        self,
        rng: Rng,
        cfg: ApproverSettings,
        log: ApprovalLog,
        scheduler: Scheduler,
        clock: Clock,
        trace: TraceRecorder,
        notify: Callable[[str], None],
    ) -> None:
        self._rng = rng
        self._cfg = cfg
        self._log = log
        self._scheduler = scheduler
        self._clock = clock
        self._trace = trace
        self._notify = notify

    def request(self, workflow_id: str, amount: int) -> None:
        if self._log.status(workflow_id) != "none":
            return
        self._log.request(workflow_id, amount)
        stream = self._rng.fork("approval").fork(workflow_id)
        delay = stream.randint(self._cfg.delay_min, self._cfg.delay_max)
        total = self._cfg.approve_weight + self._cfg.deny_weight
        approved = stream.randint(1, total) <= self._cfg.approve_weight

        def decide() -> None:
            self._log.record(workflow_id, approved)
            data: dict[str, Json] = {"approved": approved, "amount": amount}
            self._trace.record(
                self._clock.now, workflow_id, "human", "approval.decided", data
            )
            self._notify(workflow_id)

        self._scheduler.schedule_in(delay, decide)


class PolicyEngine:
    """Evaluates the configured rules before a side effect. Writes one row per rule."""

    def __init__(
        self,
        cfg: PolicyConfig,
        store: Store,
        request: ReturnRequest,
        ledger: Ledger,
        approvals: ApprovalLog,
        trace: TraceRecorder,
        clock: Clock,
    ) -> None:
        self._cfg = cfg
        self._store = store
        self._request = request
        self._ledger = ledger
        self._approvals = approvals
        self._trace = trace
        self._clock = clock

    def evaluate(
        self, effect_type: EffectType, key: str, amount: int | None
    ) -> PolicyVerdict:
        rules = self._cfg.label if effect_type == "create_label" else self._cfg.refund
        last: PolicyVerdict | None = None
        for rule_id in rules:
            kind, reason = self._rule(rule_id, key, amount)
            decision = self._ledger.insert_policy_decision(
                self._request.workflow_id, key, rule_id, kind == "allow", reason
            )
            self._emit(effect_type, key, rule_id, kind, reason, decision.id)
            last = PolicyVerdict(kind=kind, rule_id=rule_id, decision_id=decision.id)
            if kind != "allow":
                return last
        if last is None:
            raise PolicyError(f"no rules configured for {effect_type}")
        return last

    def _rule(
        self, rule_id: RuleId, key: str, amount: int | None
    ) -> tuple[VerdictKind, str]:
        match rule_id:
            case "P1":
                return self._p1(_required(amount))
            case "P2":
                return self._p2(_required(amount))
            case "P3":
                return self._p3()
            case "P4":
                return self._p4()
            case "P5":
                return self._p5(key)
            case "P6":
                return self._p6()

    def _p1(self, amount: int) -> tuple[VerdictKind, str]:
        order = self._store.get_order(self._request.order_id)
        returned = sum(
            line.qty * line.unit_price
            for line in order.lines
            if line.sku in self._request.line_skus
        )
        if amount <= returned:
            return "allow", f"amount {amount} within returned lines {returned}"
        return "deny", f"amount {amount} exceeds returned lines {returned}"

    def _p2(self, amount: int) -> tuple[VerdictKind, str]:
        cap = self._cfg.refund_cap
        if amount <= cap:
            return "allow", f"amount {amount} within cap {cap}"
        status = self._approvals.status(self._request.workflow_id)
        if status == "approved":
            return "allow", f"amount {amount} over cap {cap}, human approved"
        if status == "denied":
            return "deny", f"amount {amount} over cap {cap}, human denied"
        return "escalate", f"amount {amount} over cap {cap}, approval {status}"

    def _p3(self) -> tuple[VerdictKind, str]:
        row = self._ledger.get_workflow(self._request.workflow_id)
        if row is None:
            raise LedgerError(f"no workflow row for {self._request.workflow_id!r}")
        if row.scan_received:
            return "allow", "return scan received"
        return "deny", "no return scan yet"

    def _p4(self) -> tuple[VerdictKind, str]:
        order = self._store.get_order(self._request.order_id)
        if order.delivered_at is None:
            return "deny", "order not delivered"
        age = self._request.requested_at - order.delivered_at
        limit = self._cfg.window_days * _DAY
        if age <= limit:
            return "allow", f"requested {age}s after delivery, window {limit}s"
        return "deny", f"requested {age}s after delivery, window {limit}s"

    def _p5(self, key: str) -> tuple[VerdictKind, str]:
        others = [
            row
            for row in self._ledger.effects_for(self._request.workflow_id)
            if row.effect_type == "create_label"
            and row.status == "CONFIRMED"
            and row.idempotency_key != key
        ]
        if others:
            return "deny", "a return label already exists for this workflow"
        return "allow", "no other return label"

    def _p6(self) -> tuple[VerdictKind, str]:
        order = self._store.get_order(self._request.order_id)
        if order.fraud_risk:
            return "deny", "order flagged for fraud risk"
        return "allow", "order not flagged for fraud risk"

    def _emit(
        self,
        effect_type: EffectType,
        key: str,
        rule_id: str,
        kind: VerdictKind,
        reason: str,
        decision_id: int,
    ) -> None:
        data: dict[str, Json] = {
            "effect_type": effect_type,
            "key": key,
            "rule_id": rule_id,
            "verdict": kind,
            "reason": reason,
            "decision_id": decision_id,
        }
        self._trace.record(
            self._clock.now,
            self._request.workflow_id,
            "policy",
            "policy.decision",
            data,
        )


def _required(amount: int | None) -> int:
    if amount is None:
        raise PolicyError("this rule needs a refund amount")
    return amount
