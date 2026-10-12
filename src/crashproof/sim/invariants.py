from collections import Counter
from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict

from crashproof.models import Order, TraceEvent
from crashproof.runtime.ledger import EffectRow, PolicyDecisionRow
from crashproof.runtime.policy import ApprovalRecord
from crashproof.world.carriers import LabelRecord
from crashproof.world.refunds import RefundRecord
from crashproof.world.store import OrderNotFound


class Violation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    invariant: Literal["I1", "I2", "I3", "I4", "I5", "I6", "I7", "I8", "I9"]
    detail: str


def check_i1(refunds: Sequence[RefundRecord]) -> list[Violation]:
    """At most one refund per reference."""
    counts = Counter(r.reference for r in refunds)
    return [
        Violation(invariant="I1", detail=f"{ref}: {counts[ref]} refunds")
        for ref in sorted(counts)
        if counts[ref] > 1
    ]


def check_i2(
    refunds: Sequence[RefundRecord], orders: Sequence[Order]
) -> list[Violation]:
    """Total refunded per order never exceeds paid_amount."""
    paid = {order.order_id: order.paid_amount for order in orders}
    totals: Counter[str] = Counter()
    for refund in refunds:
        if refund.order_id not in paid:
            raise OrderNotFound(refund.order_id)
        totals[refund.order_id] += refund.amount
    return [
        Violation(
            invariant="I2",
            detail=f"{oid}: refunded {totals[oid]} > paid {paid[oid]}",
        )
        for oid in sorted(totals)
        if totals[oid] > paid[oid]
    ]


def _refund_seq(refund: RefundRecord, events: Sequence[TraceEvent]) -> int:
    for event in events:
        if event.event == "refund.issued" and event.data.get("refund_id") == (
            refund.refund_id
        ):
            return event.seq
    raise ValueError(f"no refund.issued trace event for {refund.refund_id}")


def _first_scan_seq(reference: str, events: Sequence[TraceEvent]) -> int | None:
    for event in events:
        if (
            event.event == "event.delivered"
            and event.workflow_id == reference
            and event.data.get("type") == "return.scanned"
        ):
            return event.seq
    return None


def check_i3(
    refunds: Sequence[RefundRecord],
    events: Sequence[TraceEvent],
    oracle_eligible: bool,
) -> list[Violation]:
    """A refund needs an earlier delivered scan and an eligible order."""
    found: list[Violation] = []
    for refund in sorted(refunds, key=lambda r: r.refund_id):
        seq = _refund_seq(refund, events)
        scan = _first_scan_seq(refund.reference, events)
        if scan is None or scan > seq:
            found.append(
                Violation(invariant="I3", detail=f"refund {refund.refund_id}: no_scan")
            )
        if not oracle_eligible:
            found.append(
                Violation(
                    invariant="I3", detail=f"refund {refund.refund_id}: ineligible"
                )
            )
    return found


def check_i4(
    refunds: Sequence[RefundRecord],
    approvals: Sequence[ApprovalRecord],
    events: Sequence[TraceEvent],
    refund_cap: int,
) -> list[Violation]:
    """A refund above the cap needs a recorded approval that came before it."""
    approved = {a.workflow_id for a in approvals if a.status == "approved"}
    found: list[Violation] = []
    for refund in sorted(refunds, key=lambda r: r.refund_id):
        if refund.amount <= refund_cap:
            continue
        seq = _refund_seq(refund, events)
        decided = any(
            e.event == "approval.decided"
            and e.workflow_id == refund.reference
            and e.seq < seq
            for e in events
        )
        if refund.reference not in approved or not decided:
            found.append(
                Violation(
                    invariant="I4", detail=f"refund {refund.refund_id}: no_approval"
                )
            )
    return found


def check_i5(labels: Sequence[LabelRecord]) -> list[Violation]:
    """At most one label per reference."""
    counts = Counter(label.reference for label in labels)
    return [
        Violation(invariant="I5", detail=f"{ref}: {counts[ref]} labels")
        for ref in sorted(counts)
        if counts[ref] > 1
    ]


def check_i6(
    effects: Sequence[EffectRow],
    decisions_by_workflow: dict[str, list[PolicyDecisionRow]],
) -> list[Violation]:
    """Every CONFIRMED effect points at an allowing decision of its workflow."""
    found: list[Violation] = []
    for effect in sorted(effects, key=lambda e: e.idempotency_key):
        if effect.status != "CONFIRMED":
            continue
        decisions = decisions_by_workflow.get(effect.workflow_id, [])
        matched = effect.policy_decision_id is not None and any(
            d.id == effect.policy_decision_id
            and d.workflow_id == effect.workflow_id
            and d.allowed
            for d in decisions
        )
        if not matched:
            found.append(
                Violation(
                    invariant="I6",
                    detail=f"effect {effect.idempotency_key}: no matching decision",
                )
            )
    return found


def check_i7(workflow_terminal: dict[str, bool]) -> list[Violation]:
    """No workflow is non-terminal after 72 virtual hours of quiescence."""
    return [
        Violation(invariant="I7", detail=f"{wf}: not terminal after quiescence")
        for wf in sorted(workflow_terminal)
        if not workflow_terminal[wf]
    ]


def check_i8(
    refunds: Sequence[RefundRecord], effects: Sequence[EffectRow]
) -> list[Violation]:
    """Refunds and CONFIRMED issue_refund ledger rows match one to one by key."""
    confirmed = Counter(
        e.idempotency_key
        for e in effects
        if e.effect_type == "issue_refund" and e.status == "CONFIRMED"
    )
    refunded = Counter(r.idempotency_key for r in refunds if r.idempotency_key)
    found: list[Violation] = []
    for refund in sorted(refunds, key=lambda r: (r.idempotency_key or "", r.refund_id)):
        rows = confirmed[refund.idempotency_key] if refund.idempotency_key else 0
        if rows != 1:
            found.append(
                Violation(
                    invariant="I8",
                    detail=f"refund {refund.refund_id}: {rows} CONFIRMED ledger rows",
                )
            )
    for key in sorted(confirmed):
        if refunded[key] != 1:
            found.append(
                Violation(
                    invariant="I8",
                    detail=f"CONFIRMED row {key}: {refunded[key]} refunds",
                )
            )
    return found


def check_i9(hash_a: str, hash_b: str) -> list[Violation]:
    """Running the same seed twice gives an identical trace_hash."""
    if hash_a == hash_b:
        return []
    return [Violation(invariant="I9", detail=f"trace_hash {hash_a} != {hash_b}")]


def check_world(
    orders: Sequence[Order],
    refunds: Sequence[RefundRecord],
    labels: Sequence[LabelRecord],
    effects: Sequence[EffectRow] | None,
    *,
    trace_events: Sequence[TraceEvent] | None = None,
    oracle_eligible: bool | None = None,
    approvals: Sequence[ApprovalRecord] | None = None,
    refund_cap: int | None = None,
    decisions_by_workflow: dict[str, list[PolicyDecisionRow]] | None = None,
    workflow_terminal: dict[str, bool] | None = None,
) -> list[Violation]:
    """Run every invariant whose inputs are all provided.

    I1, I2 and I5 always run. I8 needs effects, I6 needs effects and decisions,
    I3 needs trace events and the oracle verdict, I4 needs trace events,
    approvals and the cap, I7 needs the terminal flags. Output is ordered by
    invariant id.
    """
    found = check_i1(refunds) + check_i2(refunds, orders) + check_i5(labels)
    if trace_events is not None and oracle_eligible is not None:
        found += check_i3(refunds, trace_events, oracle_eligible)
    if trace_events is not None and approvals is not None and refund_cap is not None:
        found += check_i4(refunds, approvals, trace_events, refund_cap)
    if effects is not None and decisions_by_workflow is not None:
        found += check_i6(effects, decisions_by_workflow)
    if workflow_terminal is not None:
        found += check_i7(workflow_terminal)
    if effects is not None:
        found += check_i8(refunds, effects)
    return sorted(found, key=lambda v: v.invariant)
