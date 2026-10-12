import hashlib
import sqlite3
from collections.abc import Callable
from typing import Literal

import pytest

from crashproof.clock import Clock, Scheduler
from crashproof.config import FaultProbabilities
from crashproof.ids import IdGen
from crashproof.rng import Rng
from crashproof.runtime.executor import EffectResult, Executor, idempotency_key
from crashproof.runtime.ledger import EffectRow, Ledger
from crashproof.sim.trace import TraceRecorder
from crashproof.world.carriers import CarrierA
from crashproof.world.events import EventBus
from crashproof.world.faults import FaultInjector, SimulatedCrash
from crashproof.world.refunds import RefundService

WF = "wf-1"


class KeylessCarrier(CarrierA):
    supports_keys = False


def probs(
    f1: float = 0.0, f2: float = 0.0, f3: float = 0.0, f5: float = 0.0
) -> FaultProbabilities:
    return FaultProbabilities(F1=f1, F2=f2, F3=f3, F5=f5)


class Env:
    def __init__(
        self,
        p: FaultProbabilities | None = None,
        *,
        max_attempts: int = 5,
        carrier_cls: type[CarrierA] = CarrierA,
        reconciler: Callable[[EffectRow], Literal["confirmed", "failed"]] | None = None,
    ) -> None:
        self.clock = Clock()
        self.trace = TraceRecorder()
        scheduler = Scheduler(self.clock)
        ids = IdGen(0)
        self.faults = (
            None if p is None else FaultInjector(Rng(0), p, self.clock, self.trace)
        )
        bus = EventBus(scheduler, self.clock, ids, self.trace, faults=self.faults)
        self.carrier = carrier_cls(self.clock, ids, self.trace, bus, faults=self.faults)
        self.refunds = RefundService(
            self.clock, ids, self.trace, bus, faults=self.faults
        )
        self.ledger = Ledger(sqlite3.connect(":memory:"), self.clock)
        self.executor = Executor(
            self.ledger,
            self.carrier,
            self.refunds,
            self.faults,
            self.trace,
            self.clock,
            max_attempts,
            reconciler,
        )

    def names(self) -> list[str]:
        return [e.event for e in self.trace.events]

    def label(self) -> EffectResult:
        return self.executor.execute(WF, "create_label", "o1")

    def refund(self, amount: int | None = 100) -> EffectResult:
        return self.executor.execute(WF, "issue_refund", "o1", amount)

    def row(self, effect_type: Literal["create_label", "issue_refund"]) -> EffectRow:
        row = self.ledger.get_effect(idempotency_key(WF, effect_type))
        assert row is not None
        return row


def test_idempotency_key_is_sha256_of_workflow_type_step() -> None:
    expected = hashlib.sha256(f"{WF}|create_label|label".encode()).hexdigest()
    assert idempotency_key(WF, "create_label") == expected
    expected = hashlib.sha256(f"{WF}|issue_refund|refund".encode()).hexdigest()
    assert idempotency_key(WF, "issue_refund") == expected


def test_label_happy_path() -> None:
    env = Env()
    result = env.label()
    assert result.kind == "confirmed"
    assert set(result.data) == {"label_id", "status"}
    (record,) = env.carrier.labels
    assert record.idempotency_key == idempotency_key(WF, "create_label")
    assert record.reference == WF
    row = env.row("create_label")
    assert row.status == "CONFIRMED"
    assert row.attempts == 1
    assert row.request == {"order_id": "o1", "reference": WF}
    assert row.response == result.data


def test_refund_happy_path() -> None:
    env = Env()
    result = env.refund(250)
    assert result.kind == "confirmed"
    assert set(result.data) == {"refund_id", "amount"}
    assert result.data["amount"] == 250
    (record,) = env.refunds.refunds
    assert record.idempotency_key == idempotency_key(WF, "issue_refund")
    assert record.reference == WF
    row = env.row("issue_refund")
    assert row.status == "CONFIRMED"
    assert row.request == {"order_id": "o1", "reference": WF, "amount": 250}


def test_dedup_returns_stored_response_without_provider_call() -> None:
    env = Env()
    first = env.label()
    before = len(env.trace.events)
    again = env.label()
    assert again.kind == "confirmed"
    assert again.data == first.data
    assert len(env.carrier.labels) == 1
    added = [e.event for e in env.trace.events[before:]]
    assert added == ["executor.dedup"]
    assert env.row("create_label").attempts == 1


def test_request_mismatch_fails_and_calls_nothing() -> None:
    env = Env()
    env.refund(100)
    before = len(env.trace.events)
    result = env.refund(200)
    assert result.kind == "failed"
    assert result.error == "request_mismatch"
    assert len(env.refunds.refunds) == 1
    assert "refund.key_conflict" not in env.names()
    assert [e.event for e in env.trace.events[before:]] == ["executor.failed"]
    assert env.row("issue_refund").status == "CONFIRMED"


def test_f2_leaves_effect_applied_and_row_unknown() -> None:
    env = Env(probs(f2=1.0))
    result = env.label()
    assert result.kind == "timed_out"
    assert len(env.carrier.labels) == 1
    assert env.row("create_label").status == "UNKNOWN"
    assert "executor.unknown" in env.names()


def test_unknown_without_reconciler_is_never_retried() -> None:
    env = Env(probs(f2=1.0))
    env.label()
    for _ in range(3):
        assert env.label().kind == "timed_out"
    assert len(env.carrier.labels) == 1
    assert env.names().count("fault.F2") == 1
    assert env.names().count("executor.unknown_pending") == 3
    assert env.row("create_label").attempts == 1


def test_f1_timeout_marks_unknown_with_nothing_applied() -> None:
    env = Env(probs(f1=1.0))
    assert env.label().kind == "timed_out"
    assert env.carrier.labels == ()
    assert env.row("create_label").status == "UNKNOWN"


def test_forced_f5_crash_leaves_row_executing() -> None:
    env = Env(probs(f5=1.0))
    with pytest.raises(SimulatedCrash):
        env.label()
    assert len(env.carrier.labels) == 1
    assert env.row("create_label").status == "EXECUTING"


def test_refund_f5_crash_point_is_refund_applied() -> None:
    env = Env(probs(f5=1.0))
    with pytest.raises(SimulatedCrash) as info:
        env.refund()
    assert info.value.point == "refund_applied"
    assert env.row("issue_refund").status == "EXECUTING"


def make_conflict(env: Env) -> None:
    key = idempotency_key(WF, "create_label")
    env.carrier.create_label("other-order", WF, idempotency_key=key)


def test_idempotency_conflict_marks_failed_then_exhausts() -> None:
    env = Env(max_attempts=2)
    make_conflict(env)
    first = env.label()
    assert (first.kind, first.error) == ("failed", "IdempotencyConflict")
    row = env.row("create_label")
    assert (row.status, row.attempts) == ("FAILED", 1)
    assert row.response == {"error": "IdempotencyConflict"}
    assert env.label().kind == "failed"
    assert env.row("create_label").attempts == 2
    conflicts = env.names().count("label.key_conflict")
    assert env.label().kind == "exhausted"
    assert env.names().count("label.key_conflict") == conflicts
    assert "executor.exhausted" in env.names()


def test_invalid_refund_amount_marks_failed() -> None:
    env = Env()
    result = env.refund(0)
    assert (result.kind, result.error) == ("failed", "InvalidRefundAmount")
    assert env.row("issue_refund").response == {"error": "InvalidRefundAmount"}
    assert env.refunds.refunds == ()


def test_carrier_without_key_support_gets_no_key() -> None:
    env = Env(carrier_cls=KeylessCarrier)
    assert env.label().kind == "confirmed"
    assert env.carrier.labels[0].idempotency_key is None
    assert env.row("create_label").idempotency_key == idempotency_key(
        WF, "create_label"
    )


def test_refund_service_always_gets_key() -> None:
    env = Env(carrier_cls=KeylessCarrier)
    env.refund()
    assert env.refunds.refunds[0].idempotency_key == idempotency_key(WF, "issue_refund")


def test_refund_without_amount_raises_and_writes_nothing() -> None:
    env = Env()
    with pytest.raises(ValueError):
        env.refund(None)
    assert env.ledger.all_effects() == []


def test_row_left_intended_is_executed() -> None:
    env = Env()
    env.ledger.insert_intended(
        WF,
        "create_label",
        idempotency_key(WF, "create_label"),
        {"order_id": "o1", "reference": WF},
    )
    assert env.label().kind == "confirmed"
    assert env.row("create_label").attempts == 1


def unknown_row(env: Env) -> str:
    key = idempotency_key(WF, "create_label")
    env.ledger.insert_intended(
        WF, "create_label", key, {"order_id": "o1", "reference": WF}
    )
    env.ledger.mark_executing(key)
    env.ledger.mark_unknown(key)
    return key


def test_reconciler_confirmed_returns_stored_response() -> None:
    stored = {"label_id": "lbl-x", "status": "created"}
    holder: list[Env] = []

    def reconciler(row: EffectRow) -> Literal["confirmed", "failed"]:
        holder[0].ledger.mark_confirmed(row.idempotency_key, stored)
        return "confirmed"

    env = Env(reconciler=reconciler)
    holder.append(env)
    unknown_row(env)
    result = env.label()
    assert (result.kind, result.data) == ("confirmed", stored)
    assert env.carrier.labels == ()


def test_reconciler_failed_falls_through_to_retry() -> None:
    holder: list[Env] = []

    def reconciler(row: EffectRow) -> Literal["confirmed", "failed"]:
        holder[0].ledger.mark_failed(row.idempotency_key, {"error": "not_applied"})
        return "failed"

    env = Env(reconciler=reconciler)
    holder.append(env)
    unknown_row(env)
    assert env.label().kind == "confirmed"
    assert len(env.carrier.labels) == 1
    assert env.row("create_label").attempts == 2
