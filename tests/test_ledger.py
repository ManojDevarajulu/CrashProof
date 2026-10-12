import sqlite3

import pytest

from crashproof.clock import Clock
from crashproof.runtime.ledger import EffectRow, Ledger, LedgerError, WorkflowRow

WF = "wf-1"
KEY = "k1"

PATHS = {
    "INTENDED": [],
    "EXECUTING": ["executing"],
    "CONFIRMED": ["executing", "confirmed"],
    "FAILED": ["executing", "failed"],
    "UNKNOWN": ["executing", "unknown"],
}
LEGAL = {
    "INTENDED": {"executing"},
    "EXECUTING": {"confirmed", "failed", "unknown"},
    "UNKNOWN": {"confirmed", "failed"},
    "FAILED": {"executing"},
    "CONFIRMED": set(),
}
RESULT_STATUS = {
    "executing": "EXECUTING",
    "confirmed": "CONFIRMED",
    "failed": "FAILED",
    "unknown": "UNKNOWN",
}


def make() -> tuple[Ledger, sqlite3.Connection, Clock]:
    clock = Clock()
    conn = sqlite3.connect(":memory:")
    return Ledger(conn, clock), conn, clock


def apply(ledger: Ledger, key: str, op: str) -> EffectRow:
    if op == "executing":
        return ledger.mark_executing(key)
    if op == "confirmed":
        return ledger.mark_confirmed(key, {"ok": True})
    if op == "failed":
        return ledger.mark_failed(key, {"error": "E"})
    return ledger.mark_unknown(key)


def reach(ledger: Ledger, status: str, key: str = KEY) -> None:
    ledger.insert_intended(WF, "create_label", key, {"order_id": "o1"})
    for op in PATHS[status]:
        apply(ledger, key, op)


def test_insert_intended_row() -> None:
    ledger, _, clock = make()
    clock.advance_to(5)
    row = ledger.insert_intended(WF, "create_label", KEY, {"order_id": "o1"})
    assert row.status == "INTENDED"
    assert row.attempts == 0
    assert row.request == {"order_id": "o1"}
    assert row.response is None
    assert row.policy_decision_id is None
    assert (row.created_vt, row.updated_vt) == (5, 5)
    assert ledger.get_effect(KEY) == row


def test_duplicate_key_raises() -> None:
    ledger, _, _ = make()
    reach(ledger, "INTENDED")
    with pytest.raises(LedgerError):
        ledger.insert_intended("wf-2", "issue_refund", KEY, {})


def test_missing_effect_is_none() -> None:
    ledger, _, _ = make()
    assert ledger.get_effect("nope") is None


@pytest.mark.parametrize("start", sorted(PATHS))
@pytest.mark.parametrize("op", sorted(RESULT_STATUS))
def test_transition_table(start: str, op: str) -> None:
    ledger, _, _ = make()
    reach(ledger, start)
    if op in LEGAL[start]:
        assert apply(ledger, KEY, op).status == RESULT_STATUS[op]
    else:
        with pytest.raises(LedgerError):
            apply(ledger, KEY, op)
        row = ledger.get_effect(KEY)
        assert row is not None
        assert row.status == start


@pytest.mark.parametrize("op", sorted(RESULT_STATUS))
def test_missing_key_raises(op: str) -> None:
    ledger, _, _ = make()
    with pytest.raises(LedgerError):
        apply(ledger, "nope", op)


def test_attempts_count_only_moves_to_executing() -> None:
    ledger, _, _ = make()
    reach(ledger, "INTENDED")
    assert ledger.mark_executing(KEY).attempts == 1
    assert ledger.mark_unknown(KEY).attempts == 1
    assert ledger.mark_failed(KEY, {"error": "E"}).attempts == 1
    assert ledger.mark_executing(KEY).attempts == 2
    assert ledger.mark_confirmed(KEY, {"ok": True}).attempts == 2


def test_updated_vt_comes_from_clock() -> None:
    ledger, _, clock = make()
    reach(ledger, "INTENDED")
    clock.advance_to(40)
    row = ledger.mark_executing(KEY)
    assert (row.created_vt, row.updated_vt) == (0, 40)


def test_responses_round_trip_and_are_canonical_json() -> None:
    ledger, conn, _ = make()
    reach(ledger, "INTENDED")
    ledger.mark_executing(KEY)
    response = {"b": [1, {"z": None, "a": True}], "a": "é"}
    row = ledger.mark_confirmed(KEY, response)
    assert row.response == response
    stored = conn.execute("SELECT request, response FROM effects").fetchone()
    assert stored[0] == '{"order_id":"o1"}'
    assert stored[1] == '{"a":"\\u00e9","b":[1,{"a":true,"z":null}]}'


def test_recover_turns_executing_into_unknown() -> None:
    ledger, _, _ = make()
    for key, status in [
        ("a", "EXECUTING"),
        ("b", "CONFIRMED"),
        ("c", "EXECUTING"),
        ("d", "FAILED"),
        ("e", "INTENDED"),
    ]:
        reach(ledger, status, key)
    assert ledger.recover() == ["a", "c"]
    statuses = {r.idempotency_key: r.status for r in ledger.all_effects()}
    assert statuses == {
        "a": "UNKNOWN",
        "b": "CONFIRMED",
        "c": "UNKNOWN",
        "d": "FAILED",
        "e": "INTENDED",
    }
    assert ledger.recover() == []


def test_effects_for_is_ordered_by_id() -> None:
    ledger, _, _ = make()
    ledger.insert_intended("wf-a", "create_label", "z", {})
    ledger.insert_intended("wf-b", "create_label", "m", {})
    ledger.insert_intended("wf-a", "issue_refund", "a", {})
    assert [r.idempotency_key for r in ledger.effects_for("wf-a")] == ["z", "a"]
    assert [r.id for r in ledger.all_effects()] == sorted(
        r.id for r in ledger.all_effects()
    )
    assert len(ledger.all_effects()) == 3
    assert ledger.effects_for("wf-none") == []


def test_rebuilt_ledger_on_same_connection_sees_same_rows() -> None:
    ledger, conn, clock = make()
    reach(ledger, "UNKNOWN")
    ledger.upsert_workflow(WF, "LABEL_PENDING", None)
    rebuilt = Ledger(conn, clock)
    assert rebuilt.all_effects() == ledger.all_effects()
    assert rebuilt.get_workflow(WF) == ledger.get_workflow(WF)


def test_workflow_rows() -> None:
    ledger, _, clock = make()
    assert ledger.get_workflow(WF) is None
    ledger.upsert_workflow(WF, "RECEIVED", None)
    assert ledger.get_workflow(WF) == WorkflowRow(
        workflow_id=WF,
        state="RECEIVED",
        outcome=None,
        scan_received=False,
        updated_vt=0,
    )
    clock.advance_to(9)
    ledger.set_scan_received(WF)
    ledger.upsert_workflow(WF, "CLOSED", "closed")
    row = ledger.get_workflow(WF)
    assert row is not None
    assert (row.state, row.outcome, row.scan_received, row.updated_vt) == (
        "CLOSED",
        "closed",
        True,
        9,
    )
    ledger.set_outcome(WF, "gave_up")
    row = ledger.get_workflow(WF)
    assert row is not None
    assert row.outcome == "gave_up"


def test_workflow_setters_on_missing_row_raise() -> None:
    ledger, _, _ = make()
    with pytest.raises(LedgerError):
        ledger.set_scan_received(WF)
    with pytest.raises(LedgerError):
        ledger.set_outcome(WF, "x")
