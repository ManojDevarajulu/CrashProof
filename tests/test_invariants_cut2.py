import pytest

from crashproof.models import Line, Order, TraceEvent
from crashproof.runtime.ledger import EffectRow, PolicyDecisionRow
from crashproof.runtime.policy import ApprovalRecord
from crashproof.sim.invariants import (
    check_i3,
    check_i4,
    check_i6,
    check_i7,
    check_world,
)
from crashproof.world.refunds import RefundRecord

WF = "wf-1"
CAP = 20000


def refund(amount: int = 100, rid: str = "r1", ref: str = WF) -> RefundRecord:
    return RefundRecord(
        refund_id=rid,
        order_id="o1",
        amount=amount,
        reference=ref,
        idempotency_key=None,
        created_vt=0,
    )


def ev(seq: int, name: str, data: dict[str, str | bool], wf: str = WF) -> TraceEvent:
    return TraceEvent(
        seq=seq, vt=seq, workflow_id=wf, actor="world", event=name, data=dict(data)
    )


def scan(seq: int, wf: str = WF) -> TraceEvent:
    return ev(seq, "event.delivered", {"event_id": "e", "type": "return.scanned"}, wf)


def issued(seq: int, rid: str = "r1") -> TraceEvent:
    return ev(seq, "refund.issued", {"refund_id": rid})


def decided(seq: int) -> TraceEvent:
    return ev(seq, "approval.decided", {"approved": True})


def approval(status: str = "approved") -> ApprovalRecord:
    return ApprovalRecord.model_validate(
        {"workflow_id": WF, "amount": 50000, "status": status}
    )


# ---- I3 -----------------------------------------------------------------


def test_i3_no_scan() -> None:
    found = check_i3([refund()], [issued(0)], True)
    assert [(v.invariant, v.detail) for v in found] == [("I3", "refund r1: no_scan")]


def test_i3_scan_after_the_refund_is_no_scan() -> None:
    assert check_i3([refund()], [issued(0), scan(1)], True)


def test_i3_scan_for_another_workflow_does_not_count() -> None:
    assert check_i3([refund()], [scan(0, "wf-2"), issued(1)], True)


def test_i3_ineligible() -> None:
    found = check_i3([refund()], [scan(0), issued(1)], False)
    assert [v.detail for v in found] == ["refund r1: ineligible"]


def test_i3_reports_both_reasons() -> None:
    assert len(check_i3([refund()], [issued(0)], False)) == 2


def test_i3_clean() -> None:
    assert check_i3([refund()], [scan(0), issued(1)], True) == []
    assert check_i3([], [], False) == []


def test_i3_uses_the_first_scan_delivery() -> None:
    assert check_i3([refund()], [scan(0), scan(1), issued(2)], True) == []


def test_i3_missing_refund_event_is_an_error() -> None:
    with pytest.raises(ValueError):
        check_i3([refund()], [scan(0)], True)


# ---- I4 -----------------------------------------------------------------


def test_i4_no_approval() -> None:
    found = check_i4([refund(CAP + 1)], [], [issued(0)], CAP)
    assert [(v.invariant, v.detail) for v in found] == [
        ("I4", "refund r1: no_approval")
    ]


def test_i4_approved_record_but_event_after_the_refund() -> None:
    events = [issued(0), decided(1)]
    assert check_i4([refund(CAP + 1)], [approval()], events, CAP)


def test_i4_approved_record_without_an_event() -> None:
    assert check_i4([refund(CAP + 1)], [approval()], [issued(0)], CAP)


def test_i4_denied_record() -> None:
    events = [decided(0), issued(1)]
    assert check_i4([refund(CAP + 1)], [approval("denied")], events, CAP)


def test_i4_pending_record() -> None:
    events = [decided(0), issued(1)]
    assert check_i4([refund(CAP + 1)], [approval("pending")], events, CAP)


def test_i4_clean() -> None:
    events = [decided(0), issued(1)]
    assert check_i4([refund(CAP + 1)], [approval()], events, CAP) == []


def test_i4_amount_equal_to_the_cap_is_not_flagged() -> None:
    assert check_i4([refund(CAP)], [], [issued(0)], CAP) == []


# ---- I6 -----------------------------------------------------------------


def effect(status: str = "CONFIRMED", decision: int | None = 1) -> EffectRow:
    return EffectRow.model_validate(
        {
            "id": 1,
            "workflow_id": WF,
            "effect_type": "issue_refund",
            "idempotency_key": "k",
            "status": status,
            "request": {},
            "response": None,
            "policy_decision_id": decision,
            "attempts": 1,
            "created_vt": 0,
            "updated_vt": 0,
        }
    )


def decision(did: int = 1, wf: str = WF, allowed: bool = True) -> PolicyDecisionRow:
    return PolicyDecisionRow(
        id=did,
        workflow_id=wf,
        effect_key="k",
        rule_id="P1",
        allowed=allowed,
        reason="r",
        vt=0,
    )


def test_i6_confirmed_effect_without_a_decision_id() -> None:
    found = check_i6([effect(decision=None)], {WF: [decision()]})
    assert [v.invariant for v in found] == ["I6"]


def test_i6_decision_for_a_different_workflow() -> None:
    assert check_i6([effect()], {WF: [decision(wf="wf-2")]})


def test_i6_decision_that_did_not_allow() -> None:
    assert check_i6([effect()], {WF: [decision(allowed=False)]})


def test_i6_decision_id_not_present() -> None:
    assert check_i6([effect(decision=5)], {WF: [decision()]})


def test_i6_clean_and_unconfirmed_effects_are_ignored() -> None:
    assert check_i6([effect()], {WF: [decision()]}) == []
    assert check_i6([effect("FAILED", None), effect("UNKNOWN", None)], {}) == []


# ---- I7 -----------------------------------------------------------------


def test_i7_one_stuck() -> None:
    found = check_i7({"wf-2": False, "wf-1": True, "wf-0": False})
    assert [v.detail.split(":")[0] for v in found] == ["wf-0", "wf-2"]


def test_i7_none_stuck() -> None:
    assert check_i7({"wf-1": True}) == []
    assert check_i7({}) == []


# ---- check_world --------------------------------------------------------

ORDER = Order(
    order_id="o1",
    customer_id="c",
    lines=[Line(sku="a", qty=1, unit_price=100)],
    paid_amount=100,
    delivered_at=0,
    carrier="A",
    fraud_risk=False,
)


def names(found: list) -> list[str]:  # type: ignore[type-arg]
    return [v.invariant for v in found]


def test_check_world_runs_a_new_invariant_only_when_its_inputs_are_given() -> None:
    refunds = [refund()]
    events = [issued(0)]
    assert check_world([ORDER], refunds, [], None) == []
    stuck = check_world([ORDER], refunds, [], None, workflow_terminal={WF: False})
    assert names(stuck) == ["I7"]
    assert check_world([ORDER], refunds, [], None, trace_events=events) == []
    assert check_world([ORDER], refunds, [], None, oracle_eligible=False) == []
    with_oracle = check_world(
        [ORDER], refunds, [], None, trace_events=events, oracle_eligible=True
    )
    assert names(with_oracle) == ["I3"]
    big = [refund(CAP + 1)]
    assert "I4" not in names(check_world([ORDER], big, [], None, trace_events=events))
    with_cap = check_world(
        [ORDER],
        big,
        [],
        None,
        trace_events=events,
        approvals=[],
        refund_cap=CAP,
    )
    assert "I4" in names(with_cap)
    bad = [effect(decision=None)]
    assert "I6" not in names(check_world([ORDER], [], [], bad))
    with_decisions = check_world([ORDER], [], [], bad, decisions_by_workflow={WF: []})
    assert "I6" in names(with_decisions)
    without_effects = check_world([ORDER], [], [], None, decisions_by_workflow={WF: []})
    assert "I6" not in names(without_effects)


def test_check_world_orders_output_by_invariant_id() -> None:
    found = check_world(
        [ORDER],
        [refund(), refund(rid="r2")],
        [],
        None,
        trace_events=[issued(0), issued(1, "r2")],
        oracle_eligible=False,
        workflow_terminal={WF: False},
    )
    ids = names(found)
    assert ids == sorted(ids)
    assert {"I1", "I2", "I3", "I7"} <= set(ids)
