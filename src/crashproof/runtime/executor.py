import hashlib
from collections.abc import Callable
from typing import Literal

from pydantic import BaseModel, ConfigDict

from crashproof.clock import Clock
from crashproof.models import Json
from crashproof.runtime.ledger import EffectRow, EffectType, Ledger, LedgerError
from crashproof.runtime.policy import PolicyEngine, PolicyVerdict
from crashproof.sim.trace import TraceRecorder
from crashproof.world.carriers import IdempotencyConflict, LabelProvider
from crashproof.world.faults import CrashPoint, FaultInjector, Timeout
from crashproof.world.refunds import InvalidRefundAmount, RefundService

_LOGICAL_STEP: dict[EffectType, str] = {
    "create_label": "label",
    "issue_refund": "refund",
}
_CRASH_POINT: dict[EffectType, CrashPoint] = {
    "create_label": "label_applied",
    "issue_refund": "refund_applied",
}

type Reconciler = Callable[[EffectRow], Literal["confirmed", "failed"]]


def idempotency_key(workflow_id: str, effect_type: EffectType) -> str:
    step = _LOGICAL_STEP[effect_type]
    return hashlib.sha256(f"{workflow_id}|{effect_type}|{step}".encode()).hexdigest()


class EffectResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    kind: Literal[
        "confirmed", "timed_out", "failed", "exhausted", "denied", "escalated"
    ]
    data: dict[str, Json] = {}
    error: str | None = None


class Executor:
    """Runs each side effect through the ledger: INTENDED, EXECUTING, then a verdict.

    An UNKNOWN effect is never retried blindly: without a reconciler it reports
    a timeout and makes no provider call. A SimulatedCrash raised at the crash
    point is never caught here, so the row stays EXECUTING for recover().
    """

    def __init__(
        self,
        ledger: Ledger,
        carrier: LabelProvider,
        refunds: RefundService,
        faults: FaultInjector | None,
        trace: TraceRecorder,
        clock: Clock,
        max_attempts: int,
        reconciler: Reconciler | None = None,
        *,
        policy: PolicyEngine | None = None,
    ) -> None:
        self._ledger = ledger
        self._carrier = carrier
        self._refunds = refunds
        self._faults = faults
        self._trace = trace
        self._clock = clock
        self._max_attempts = max_attempts
        self._reconciler = reconciler
        self._policy = policy

    def execute(
        self,
        workflow_id: str,
        effect_type: EffectType,
        order_id: str,
        amount: int | None = None,
    ) -> EffectResult:
        request = _request(workflow_id, effect_type, order_id, amount)
        key = idempotency_key(workflow_id, effect_type)
        row = self._ledger.get_effect(key)
        if row is None:
            row = self._ledger.insert_intended(workflow_id, effect_type, key, request)
        if row.status == "CONFIRMED":
            return self._dedup(row, request)
        if row.status == "UNKNOWN":
            if self._reconciler is None:
                self._emit(workflow_id, "executor.unknown_pending", row)
                return EffectResult(kind="timed_out")
            if self._reconciler(row) == "confirmed":
                return EffectResult(kind="confirmed", data=_stored(self._require(key)))
            row = self._require(key)
        if row.attempts >= self._max_attempts:
            self._emit(workflow_id, "executor.exhausted", row)
            return EffectResult(kind="exhausted")
        return self._call(row, order_id, amount)

    def _dedup(self, row: EffectRow, request: dict[str, Json]) -> EffectResult:
        if row.request != request:
            self._emit(
                row.workflow_id,
                "executor.failed",
                row,
                {"error": "request_mismatch"},
            )
            return EffectResult(kind="failed", error="request_mismatch")
        self._emit(row.workflow_id, "executor.dedup", row)
        return EffectResult(kind="confirmed", data=_stored(row))

    def _call(self, row: EffectRow, order_id: str, amount: int | None) -> EffectResult:
        key = row.idempotency_key
        wf = row.workflow_id
        decision_id: int | None = None
        if self._policy is not None:
            verdict = self._policy.evaluate(row.effect_type, key, amount)
            if verdict.kind == "deny":
                return self._deny(row, verdict)
            if verdict.kind == "escalate":
                self._emit(wf, "executor.escalated", row, {"rule_id": verdict.rule_id})
                return EffectResult(
                    kind="escalated",
                    data={"rule_id": verdict.rule_id},
                    error="escalated",
                )
            decision_id = verdict.decision_id
        self._ledger.mark_executing(key, decision_id)
        try:
            response = self._provider_call(row, order_id, amount)
        except Timeout:
            self._ledger.mark_unknown(key)
            self._emit(wf, "executor.unknown", row)
            return EffectResult(kind="timed_out")
        except (IdempotencyConflict, InvalidRefundAmount) as exc:
            error = type(exc).__name__
            self._ledger.mark_failed(key, {"error": error})
            self._emit(wf, "executor.failed", row, {"error": error})
            return EffectResult(kind="failed", error=error)
        if self._faults is not None:
            self._faults.crash_point(_CRASH_POINT[row.effect_type], wf)
        self._ledger.mark_confirmed(key, response)
        self._emit(wf, "executor.confirmed", row)
        return EffectResult(kind="confirmed", data=response)

    def _deny(self, row: EffectRow, verdict: PolicyVerdict) -> EffectResult:
        response: dict[str, Json] = {
            "error": "policy_denied",
            "rule_id": verdict.rule_id,
        }
        self._ledger.mark_denied(row.idempotency_key, verdict.decision_id, response)
        self._emit(row.workflow_id, "executor.denied", row, response)
        return EffectResult(
            kind="denied",
            data={"rule_id": verdict.rule_id},
            error=f"policy_denied:{verdict.rule_id}",
        )

    def _provider_call(
        self, row: EffectRow, order_id: str, amount: int | None
    ) -> dict[str, Json]:
        wf = row.workflow_id
        key = row.idempotency_key
        if row.effect_type == "create_label":
            label = self._carrier.create_label(
                order_id,
                wf,
                idempotency_key=key if self._carrier.supports_keys else None,
            )
            return {"label_id": label.label_id, "status": label.status}
        if amount is None:
            raise ValueError("issue_refund requires an amount")
        record = self._refunds.issue_refund(order_id, amount, wf, idempotency_key=key)
        return {"refund_id": record.refund_id, "amount": record.amount}

    def _require(self, key: str) -> EffectRow:
        row = self._ledger.get_effect(key)
        if row is None:
            raise LedgerError(f"effect {key!r} vanished from the ledger")
        return row

    def _emit(
        self,
        workflow_id: str,
        event: str,
        row: EffectRow,
        extra: dict[str, Json] | None = None,
    ) -> None:
        data: dict[str, Json] = {
            "effect_type": row.effect_type,
            "key": row.idempotency_key,
            **(extra or {}),
        }
        self._trace.record(self._clock.now, workflow_id, "runtime", event, data)


def _request(
    workflow_id: str, effect_type: EffectType, order_id: str, amount: int | None
) -> dict[str, Json]:
    request: dict[str, Json] = {"order_id": order_id, "reference": workflow_id}
    if effect_type == "issue_refund":
        if amount is None:
            raise ValueError("issue_refund requires an amount")
        request["amount"] = amount
    return request


def _stored(row: EffectRow) -> dict[str, Json]:
    if row.response is None:
        raise LedgerError(f"effect {row.idempotency_key!r} has no stored response")
    return row.response
