import pytest

from crashproof.agent.base import Action, AgentError, Done, Observation, Wait
from crashproof.agent.scripted import ScriptedAgent
from crashproof.models import Line, Order, ReturnRequest
from crashproof.runtime.tools import ToolCall, ToolResult

ORDER = Order(
    order_id="o1",
    customer_id="c1",
    lines=[
        Line(sku="a", qty=2, unit_price=1500),
        Line(sku="b", qty=1, unit_price=700),
    ],
    paid_amount=3700,
    delivered_at=0,
    carrier="A",
    fraud_risk=False,
)
GET_ORDER = ToolCall(tool="get_order", args={"order_id": "o1"})
CHECK = ToolCall(tool="check_policy", args={"order_id": "o1"})
LABEL = ToolCall(tool="create_return_label", args={"order_id": "o1"})
CLOSE = ToolCall(tool="close_case", args={})
OK = ToolResult(ok=True, data={})
ORDER_OK = ToolResult(ok=True, data={"order": ORDER.model_dump(mode="json")})


def refund(amount: int) -> ToolCall:
    return ToolCall(tool="issue_refund", args={"order_id": "o1", "amount": amount})


def request(skus: list[str]) -> ReturnRequest:
    return ReturnRequest(
        workflow_id="wf-1", order_id="o1", line_skus=skus, reason="r", requested_at=10
    )


def obs(
    call: ToolCall | None,
    result: ToolResult | None = OK,
    *,
    timed_out: bool = False,
    scan: bool = False,
) -> Observation:
    return Observation(
        last_call=call,
        last_result=None if timed_out else result,
        timed_out=timed_out,
        scan_received=scan,
    )


def first() -> Observation:
    return Observation(
        last_call=None, last_result=None, timed_out=False, scan_received=False
    )


def to_label(agent: ScriptedAgent) -> None:
    assert agent.next_action(first()) == GET_ORDER
    assert agent.next_action(obs(GET_ORDER, ORDER_OK)) == CHECK
    assert agent.next_action(obs(CHECK)) == LABEL


def test_happy_path() -> None:
    agent = ScriptedAgent(request(["a", "b"]))
    to_label(agent)
    assert agent.next_action(obs(LABEL)) == Wait()
    assert agent.next_action(obs(LABEL)) == Wait()
    assert agent.next_action(obs(LABEL, scan=True)) == refund(3700)
    assert agent.next_action(obs(refund(3700), scan=True)) == CLOSE
    assert agent.next_action(obs(CLOSE, scan=True)) == Done()


def test_partial_return_amount() -> None:
    agent = ScriptedAgent(request(["a"]))
    to_label(agent)
    assert agent.next_action(obs(LABEL, scan=True)) == refund(3000)


def test_wait_then_refund_only_after_scan() -> None:
    agent = ScriptedAgent(request(["b"]))
    to_label(agent)
    assert agent.next_action(obs(LABEL, scan=False)) == Wait()
    assert agent.next_action(obs(LABEL, scan=True)) == refund(700)


def test_scan_already_received_with_label_goes_straight_to_refund() -> None:
    agent = ScriptedAgent(request(["b"]))
    to_label(agent)
    assert agent.next_action(obs(LABEL, scan=True)) == refund(700)


def test_duplicate_scan_never_second_refund() -> None:
    agent = ScriptedAgent(request(["a", "b"]))
    to_label(agent)
    actions: list[Action] = [agent.next_action(obs(LABEL, scan=True))]
    actions.append(agent.next_action(obs(refund(3700), scan=True)))
    actions.append(agent.next_action(obs(CLOSE, scan=True)))
    actions.append(agent.next_action(obs(CLOSE, scan=True)))
    assert actions[0] == refund(3700)
    assert sum(1 for a in actions if a == refund(3700)) == 1


def test_timeout_retries_each_call() -> None:
    agent = ScriptedAgent(request(["a", "b"]))
    assert agent.next_action(first()) == GET_ORDER
    assert agent.next_action(obs(GET_ORDER, timed_out=True)) == GET_ORDER
    assert agent.next_action(obs(GET_ORDER, ORDER_OK)) == CHECK
    assert agent.next_action(obs(CHECK)) == LABEL
    assert agent.next_action(obs(LABEL, timed_out=True)) == LABEL
    assert agent.next_action(obs(LABEL, scan=True)) == refund(3700)
    assert agent.next_action(obs(refund(3700), timed_out=True, scan=True)) == refund(
        3700
    )
    assert agent.next_action(obs(refund(3700), scan=True)) == CLOSE


@pytest.mark.parametrize(("max_retries", "attempts"), [(3, 4), (0, 1), (1, 2)])
def test_retries_exhausted_then_done(max_retries: int, attempts: int) -> None:
    agent = ScriptedAgent(request(["a"]), max_retries=max_retries)
    assert agent.next_action(first()) == GET_ORDER
    proposals = 1
    action: Action = GET_ORDER
    while action == GET_ORDER:
        action = agent.next_action(obs(GET_ORDER, timed_out=True))
        proposals += action == GET_ORDER
    assert proposals == attempts
    assert action == Done()


def test_retry_counter_resets_after_success() -> None:
    agent = ScriptedAgent(request(["a"]), max_retries=1)
    assert agent.next_action(first()) == GET_ORDER
    assert agent.next_action(obs(GET_ORDER, timed_out=True)) == GET_ORDER
    assert agent.next_action(obs(GET_ORDER, ORDER_OK)) == CHECK
    assert agent.next_action(obs(CHECK, timed_out=True)) == CHECK
    assert agent.next_action(obs(CHECK)) == LABEL
    assert agent.next_action(obs(LABEL, timed_out=True)) == LABEL
    assert agent.next_action(obs(LABEL, timed_out=True)) == Done()


def test_not_ok_gives_done() -> None:
    bad = ToolResult(ok=False, data={}, error="denied")
    agent = ScriptedAgent(request(["a"]))
    assert agent.next_action(first()) == GET_ORDER
    assert agent.next_action(obs(GET_ORDER, ORDER_OK)) == CHECK
    assert agent.next_action(obs(CHECK, bad)) == Done()


def test_unknown_sku_gives_done() -> None:
    agent = ScriptedAgent(request(["zzz"]))
    assert agent.next_action(first()) == GET_ORDER
    assert agent.next_action(obs(GET_ORDER, ORDER_OK)) == Done()


def test_mismatched_last_call_raises() -> None:
    agent = ScriptedAgent(request(["a"]))
    assert agent.next_action(first()) == GET_ORDER
    with pytest.raises(AgentError):
        agent.next_action(obs(CHECK))


def test_first_observation_with_last_call_raises() -> None:
    with pytest.raises(AgentError):
        ScriptedAgent(request(["a"])).next_action(obs(GET_ORDER))


def test_missing_result_without_timeout_raises() -> None:
    agent = ScriptedAgent(request(["a"]))
    assert agent.next_action(first()) == GET_ORDER
    bad = Observation(
        last_call=GET_ORDER, last_result=None, timed_out=False, scan_received=False
    )
    with pytest.raises(AgentError):
        agent.next_action(bad)


def test_identical_sequences_give_identical_actions() -> None:
    sequence = [
        first(),
        obs(GET_ORDER, timed_out=True),
        obs(GET_ORDER, ORDER_OK),
        obs(CHECK),
        obs(LABEL),
        obs(LABEL, scan=True),
        obs(refund(3700), scan=True),
        obs(CLOSE, scan=True),
    ]
    first_agent, second_agent = (ScriptedAgent(request(["a", "b"])) for _ in range(2))
    assert [first_agent.next_action(o) for o in sequence] == [
        second_agent.next_action(o) for o in sequence
    ]


def test_done_is_sticky() -> None:
    agent = ScriptedAgent(request(["zzz"]))
    assert agent.next_action(first()) == GET_ORDER
    assert agent.next_action(obs(GET_ORDER, ORDER_OK)) == Done()
    assert agent.next_action(obs(GET_ORDER, ORDER_OK)) == Done()
    assert agent.next_action(first()) == Done()
