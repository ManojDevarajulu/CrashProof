from typing import Literal

from crashproof.clock import Clock
from crashproof.models import Json
from crashproof.runtime.executor import Reconciler
from crashproof.runtime.ledger import EffectRow, Ledger, LedgerError
from crashproof.sim.trace import TraceRecorder
from crashproof.world.carriers import LabelProvider
from crashproof.world.refunds import RefundService

_NOT_APPLIED: dict[str, Json] = {"error": "not_applied"}


def make_reconciler(
    ledger: Ledger,
    carrier: LabelProvider,
    refunds: RefundService,
    trace: TraceRecorder,
    clock: Clock,
) -> Reconciler:
    """Resolve an UNKNOWN effect by asking the provider, never by calling it again.

    Uses lookup methods only, so it cannot change the world and is never faulted.
    A key lookup is used wherever the provider supports keys; the lookup by
    reference is used only for a carrier without keys.
    """

    def reconcile(row: EffectRow) -> Literal["confirmed", "failed"]:
        if row.status != "UNKNOWN":
            raise LedgerError(f"cannot reconcile a {row.status} effect")
        found = _lookup(row, carrier, refunds)
        data: dict[str, Json] = {
            "effect_type": row.effect_type,
            "key": row.idempotency_key,
        }
        if found is None:
            ledger.mark_failed(row.idempotency_key, _NOT_APPLIED)
            trace.record(
                clock.now, row.workflow_id, "runtime", "reconcile.not_applied", data
            )
            return "failed"
        ledger.mark_confirmed(row.idempotency_key, found)
        trace.record(clock.now, row.workflow_id, "runtime", "reconcile.applied", data)
        return "confirmed"

    return reconcile


def reconcile_workflow(workflow_id: str, ledger: Ledger, reconciler: Reconciler) -> int:
    """Recover EXECUTING rows, then resolve every UNKNOWN effect in id order."""
    ledger.recover()
    resolved = 0
    for row in ledger.effects_for(workflow_id):
        if row.status == "UNKNOWN":
            reconciler(row)
            resolved += 1
    return resolved


def _lookup(
    row: EffectRow, carrier: LabelProvider, refunds: RefundService
) -> dict[str, Json] | None:
    if row.effect_type == "issue_refund":
        refund = refunds.get_by_key(row.idempotency_key)
        if refund is None:
            return None
        return {"refund_id": refund.refund_id, "amount": refund.amount}
    if carrier.supports_keys:
        label = carrier.get_label_by_key(row.idempotency_key)
    else:
        labels = carrier.get_label_by_reference(row.workflow_id)
        label = labels[0] if labels else None
    if label is None:
        return None
    return {"label_id": label.label_id, "status": label.status}
