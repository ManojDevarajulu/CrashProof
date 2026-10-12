import pytest

from crashproof.models import Line, Order
from crashproof.runtime.ledger import EffectRow, EffectStatus, EffectType
from crashproof.sim.invariants import (
    check_i1,
    check_i2,
    check_i5,
    check_i8,
    check_i9,
    check_world,
)
from crashproof.world.carriers import LabelRecord
from crashproof.world.refunds import RefundRecord
from crashproof.world.store import OrderNotFound

ORDER = Order(
    order_id="ord-1",
    customer_id="cus-1",
    lines=[Line(sku="sku-1", qty=2, unit_price=500)],
    paid_amount=1000,
    delivered_at=0,
    carrier="A",
    fraud_risk=False,
)


def refund(
    n: int, amount: int = 1000, ref: str = "wf-1", key: str | None = "k1"
) -> RefundRecord:
    return RefundRecord(
        refund_id=f"rfd-{n}",
        order_id="ord-1",
        amount=amount,
        reference=ref,
        idempotency_key=key,
        created_vt=0,
    )


def label(n: int, ref: str = "wf-1") -> LabelRecord:
    return LabelRecord(
        label_id=f"lbl-{n}",
        order_id="ord-1",
        reference=ref,
        idempotency_key=None,
        status="created",
        created_vt=0,
    )


def row(
    key: str,
    status: EffectStatus = "CONFIRMED",
    effect_type: EffectType = "issue_refund",
) -> EffectRow:
    return EffectRow(
        id=1,
        workflow_id="wf-1",
        effect_type=effect_type,
        idempotency_key=key,
        status=status,
        request={},
        response=None,
        policy_decision_id=None,
        attempts=1,
        created_vt=0,
        updated_vt=0,
    )


def test_i1_flags_two_refunds_for_one_reference() -> None:
    found = check_i1([refund(1, 500, key="a"), refund(2, 500, key="b")])
    assert [v.invariant for v in found] == ["I1"]
    assert "wf-1" in found[0].detail


def test_i1_clean_state_passes() -> None:
    assert check_i1([refund(1), refund(2, ref="wf-2", key="k2")]) == []
    assert check_i1([]) == []


def test_i2_flags_total_above_paid_amount() -> None:
    found = check_i2([refund(1, 600, key="a"), refund(2, 600, key="b")], [ORDER])
    assert [v.invariant for v in found] == ["I2"]


def test_i2_clean_state_passes_at_exactly_paid_amount() -> None:
    assert check_i2([refund(1, 1000)], [ORDER]) == []
    assert check_i2([], [ORDER]) == []


def test_i2_unknown_order_raises() -> None:
    with pytest.raises(OrderNotFound):
        check_i2([refund(1)], [])


def test_i5_flags_two_labels_for_one_reference() -> None:
    found = check_i5([label(1), label(2)])
    assert [v.invariant for v in found] == ["I5"]


def test_i5_clean_state_passes() -> None:
    assert check_i5([label(1), label(2, ref="wf-2")]) == []
    assert check_i5([]) == []


def test_i8_flags_keyless_refund() -> None:
    found = check_i8([refund(1, key=None)], [])
    assert [v.invariant for v in found] == ["I8"]


def test_i8_flags_refund_with_no_confirmed_row() -> None:
    assert len(check_i8([refund(1)], [row("k1", status="UNKNOWN")])) == 1
    assert len(check_i8([refund(1)], [])) == 1


def test_i8_flags_confirmed_row_with_no_refund() -> None:
    found = check_i8([], [row("k1")])
    assert [v.invariant for v in found] == ["I8"]


def test_i8_ignores_label_rows_and_unconfirmed_rows() -> None:
    effects = [row("lk", effect_type="create_label"), row("k9", status="FAILED")]
    assert check_i8([], effects) == []


def test_i8_clean_state_passes() -> None:
    assert check_i8([refund(1)], [row("k1")]) == []
    assert check_i8([], []) == []


def test_i9_flags_differing_hashes() -> None:
    assert [v.invariant for v in check_i9("a", "b")] == ["I9"]


def test_i9_identical_hashes_pass() -> None:
    assert check_i9("a", "a") == []


def test_world_runs_i1_i2_i5_in_order_and_skips_i8_without_effects() -> None:
    refunds = [refund(1, 600, key="a"), refund(2, 600, key="b")]
    found = check_world([ORDER], refunds, [label(1), label(2)], None)
    assert [v.invariant for v in found] == ["I1", "I2", "I5"]


def test_world_adds_i8_when_effects_given() -> None:
    found = check_world([ORDER], [refund(1)], [label(1)], [])
    assert [v.invariant for v in found] == ["I8"]


def test_world_clean_state_passes() -> None:
    assert check_world([ORDER], [refund(1)], [label(1)], [row("k1")]) == []
