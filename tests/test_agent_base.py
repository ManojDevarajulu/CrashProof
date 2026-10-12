import pytest
from pydantic import ValidationError

from crashproof.agent.base import Done, Observation, Wait
from crashproof.runtime.tools import ToolCall, ToolResult

CALL = ToolCall(tool="get_order", args={"order_id": "o1"})
RESULT = ToolResult(ok=True, data={})


def test_first_observation() -> None:
    obs = Observation(
        last_call=None, last_result=None, timed_out=False, scan_received=False
    )
    assert obs.last_call is None


def test_timed_out_with_result_rejected() -> None:
    with pytest.raises(ValidationError):
        Observation(
            last_call=CALL, last_result=RESULT, timed_out=True, scan_received=False
        )


def test_timed_out_without_last_call_rejected() -> None:
    with pytest.raises(ValidationError):
        Observation(
            last_call=None, last_result=None, timed_out=True, scan_received=False
        )


def test_timed_out_valid() -> None:
    obs = Observation(
        last_call=CALL, last_result=None, timed_out=True, scan_received=False
    )
    assert obs.timed_out


def test_observation_frozen() -> None:
    obs = Observation(
        last_call=CALL, last_result=RESULT, timed_out=False, scan_received=False
    )
    with pytest.raises(ValidationError):
        obs.scan_received = True  # type: ignore[misc]


def test_wait_and_done_equality_and_frozen() -> None:
    assert Wait() == Wait()
    assert Done() == Done()
    assert Wait() != Done()
    assert Wait().kind == "wait"
    assert Done().kind == "done"
    with pytest.raises(ValidationError):
        Wait().kind = "done"  # type: ignore[misc,assignment]
    with pytest.raises(ValidationError):
        Done.model_validate({"kind": "done", "extra": 1})
