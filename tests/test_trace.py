import hashlib
from pathlib import Path

import pytest
from pydantic import ValidationError

from crashproof.sim.trace import TraceRecorder, canonical_line, serialize, trace_hash

WF = "wf-000001"

PINNED_BYTES = (
    b'{"actor":"world","data":{"order_id":"order-000001","paid_amount":3700},'
    b'"event":"order.created","seq":0,"vt":0,"workflow_id":"wf-000001"}\n'
    b'{"actor":"agent","data":{"args":{"a":[1,null,true],"b":2},'
    b'"tool":"get_order"},"event":"tool.proposed","seq":1,"vt":5,'
    b'"workflow_id":"wf-000001"}\n'
    b'{"actor":"fault","data":{"note":"caf\\u00e9"},"event":"timeout","seq":2,'
    b'"vt":5,"workflow_id":"wf-000001"}\n'
)
PINNED_DIGEST = "2411ac943c58b01a068e6d22edb73505fd63558911ed1822871e14c04b39a3fb"


def pinned_recorder() -> TraceRecorder:
    rec = TraceRecorder()
    rec.record(
        0,
        WF,
        "world",
        "order.created",
        {"paid_amount": 3700, "order_id": "order-000001"},
    )
    rec.record(
        5,
        WF,
        "agent",
        "tool.proposed",
        {"tool": "get_order", "args": {"b": 2, "a": [1, None, True]}},
    )
    rec.record(5, WF, "fault", "timeout", {"note": "café"})
    return rec


def test_canonical_line_exact_literal() -> None:
    rec = TraceRecorder()
    e = rec.record(7, WF, "runtime", "step", {"z": 1, "a": "x"})
    assert canonical_line(e) == (
        '{"actor":"runtime","data":{"a":"x","z":1},"event":"step",'
        '"seq":0,"vt":7,"workflow_id":"wf-000001"}'
    )


def test_nested_keys_sorted() -> None:
    rec = TraceRecorder()
    e = rec.record(
        0, WF, "agent", "x", {"b": {"d": 1, "c": [{"z": 1, "y": 2}]}, "a": 0}
    )
    assert '"data":{"a":0,"b":{"c":[{"y":2,"z":1}],"d":1}}' in canonical_line(e)


def test_non_ascii_escaped() -> None:
    rec = TraceRecorder()
    e = rec.record(0, WF, "agent", "x", {"n": "é"})
    line = canonical_line(e)
    assert "\\u00e9" in line
    assert line.isascii()


@pytest.mark.parametrize(
    "bad",
    [1.5, 1.0, float("nan"), (1, 2), {1, 2}, b"ab", {1: "a"}, [1.5], {"k": (1,)}],
)
def test_non_json_data_rejected(bad: object) -> None:
    rec = TraceRecorder()
    data = bad if isinstance(bad, dict) else {"k": bad}
    with pytest.raises(ValidationError):
        rec.record(0, WF, "agent", "x", data)  # type: ignore[arg-type]


def test_caller_mutation_after_record_has_no_effect() -> None:
    rec = TraceRecorder()
    data: dict[str, object] = {"inner": {"k": 1}, "lst": [1]}
    e = rec.record(0, WF, "agent", "x", data)  # type: ignore[arg-type]
    before = canonical_line(e)
    data["new"] = 1
    data["inner"]["k"] = 2  # type: ignore[index]
    data["lst"].append(2)  # type: ignore[attr-defined]
    assert canonical_line(e) == before


def test_seq_increments_from_zero() -> None:
    rec = TraceRecorder()
    seqs = [rec.record(i, WF, "agent", "x", {}).seq for i in range(3)]
    assert seqs == [0, 1, 2]
    assert [e.seq for e in rec.events] == [0, 1, 2]


def test_events_is_tuple() -> None:
    assert isinstance(TraceRecorder().events, tuple)


def test_backwards_vt_rejected_and_not_recorded() -> None:
    rec = TraceRecorder()
    rec.record(10, WF, "agent", "x", {})
    with pytest.raises(ValueError, match="backwards"):
        rec.record(9, WF, "agent", "x", {})
    assert len(rec.events) == 1
    assert rec.record(10, WF, "agent", "x", {}).seq == 1


def test_serialize_trailing_newline_once() -> None:
    out = serialize(pinned_recorder().events)
    assert out.endswith(b"\n")
    assert not out.endswith(b"\n\n")
    assert out.count(b"\n") == 3


def test_serialize_empty_is_empty_bytes() -> None:
    assert serialize([]) == b""
    assert trace_hash([]) == hashlib.sha256(b"").hexdigest()


def test_identical_recordings_give_identical_bytes() -> None:
    a = serialize(pinned_recorder().events)
    b = serialize(pinned_recorder().events)
    assert a == b
    assert trace_hash(pinned_recorder().events) == trace_hash(pinned_recorder().events)


def test_write_bytes_has_no_carriage_return(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl"
    path.write_bytes(serialize(pinned_recorder().events))
    assert b"\r" not in path.read_bytes()


def test_pinned_bytes_and_hash() -> None:
    events = pinned_recorder().events
    assert serialize(events) == PINNED_BYTES
    assert trace_hash(events) == PINNED_DIGEST
