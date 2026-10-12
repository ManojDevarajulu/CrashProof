import json
import sqlite3
from typing import Any
from unittest.mock import create_autospec

import pytest

from crashproof.agent.base import AgentError, Done, Observation, Wait
from crashproof.agent.scripted import ScriptedAgent
from crashproof.clock import Clock, Scheduler
from crashproof.config import (
    FaultProbabilities,
    ScenarioConfig,
    VariantWeights,
    load_policy_config,
    load_scenario_config,
)
from crashproof.ids import IdGen
from crashproof.models import Line, Order, ReturnRequest
from crashproof.rng import Rng
from crashproof.runtime.executor import Executor, idempotency_key
from crashproof.runtime.ledger import Ledger, LedgerError
from crashproof.runtime.policy import ApprovalLog, PolicyEngine, SimulatedApprover
from crashproof.runtime.tools import ToolCall, ToolResult
from crashproof.runtime.workflow import HardenedRuntime, Workflow, WorkflowError
from crashproof.sim import runner
from crashproof.sim.runner import RunResult, run_seed
from crashproof.sim.scenario import make_scenario
from crashproof.sim.trace import TraceRecorder
from crashproof.world.carriers import CarrierA
from crashproof.world.events import EventBus
from crashproof.world.refunds import RefundService
from crashproof.world.store import Store

WF = "wf-1"
CFG = load_scenario_config()
POLICY = load_policy_config()
ZERO = FaultProbabilities(F1=0.0, F2=0.0, F3=0.0, F5=0.0)


def order(*, fraud: bool = False, big: bool = False) -> Order:
    lines = [
        Line(sku="a", qty=2, unit_price=20000 if big else 1500),
        Line(sku="b", qty=1, unit_price=5000 if big else 700),
    ]
    return Order(
        order_id="o1",
        customer_id="c1",
        lines=lines,
        paid_amount=sum(x.qty * x.unit_price for x in lines),
        delivered_at=0,
        carrier="A",
        fraud_risk=fraud,
    )


REQUEST = ReturnRequest(
    workflow_id=WF, order_id="o1", line_skus=["a", "b"], reason="r", requested_at=10
)


class SpyCarrier(CarrierA):
    calls = 0

    def create_label(self, *args: Any, **kwargs: Any) -> Any:
        self.calls += 1
        return super().create_label(*args, **kwargs)


class SpyRefunds(RefundService):
    calls = 0

    def issue_refund(self, *args: Any, **kwargs: Any) -> Any:
        self.calls += 1
        return super().issue_refund(*args, **kwargs)


class Env:
    def __init__(
        self, *, fraud: bool = False, big: bool = False, reconciler: Any = None
    ) -> None:
        self.clock = Clock()
        self.scheduler = Scheduler(self.clock)
        self.trace = TraceRecorder()
        ids = IdGen(0)
        bus = EventBus(self.scheduler, self.clock, ids, self.trace)
        self.carrier = SpyCarrier(self.clock, ids, self.trace, bus)
        self.refunds = SpyRefunds(self.clock, ids, self.trace, bus)
        self.ledger = Ledger(sqlite3.connect(":memory:"), self.clock)
        self.ledger.upsert_workflow(WF, "RECEIVED", None)
        self.approvals = ApprovalLog()
        engine = PolicyEngine(
            POLICY,
            Store([order(fraud=fraud, big=big)]),
            REQUEST,
            self.ledger,
            self.approvals,
            self.trace,
            self.clock,
        )
        self.executor = Executor(
            self.ledger,
            self.carrier,
            self.refunds,
            None,
            self.trace,
            self.clock,
            5,
            reconciler,
            policy=engine,
        )

    def label(self) -> Any:
        return self.executor.execute(WF, "create_label", "o1")

    def refund(self, amount: int = 3700) -> Any:
        return self.executor.execute(WF, "issue_refund", "o1", amount)

    def row(self, effect_type: str) -> Any:
        return self.ledger.get_effect(idempotency_key(WF, effect_type))  # type: ignore[arg-type]


# ---- ledger -------------------------------------------------------------


def test_mark_denied_from_intended_and_failed_without_attempts() -> None:
    ledger = Ledger(sqlite3.connect(":memory:"), Clock())
    ledger.insert_intended(WF, "issue_refund", "k", {})
    row = ledger.mark_denied("k", 7, {"error": "policy_denied"})
    assert (row.status, row.attempts, row.policy_decision_id) == ("FAILED", 0, 7)
    again = ledger.mark_denied("k", 8, {"error": "policy_denied"})
    assert (again.status, again.attempts, again.policy_decision_id) == ("FAILED", 0, 8)
    assert ledger.mark_executing("k").attempts == 1


@pytest.mark.parametrize("path", [["executing"], ["executing", "confirmed"]])
def test_mark_denied_is_illegal_elsewhere(path: list[str]) -> None:
    ledger = Ledger(sqlite3.connect(":memory:"), Clock())
    ledger.insert_intended(WF, "issue_refund", "k", {})
    ledger.mark_executing("k")
    if "confirmed" in path:
        ledger.mark_confirmed("k", {})
    with pytest.raises(LedgerError):
        ledger.mark_denied("k", 1, {})


def test_policy_decision_rows_round_trip() -> None:
    clock = Clock()
    ledger = Ledger(sqlite3.connect(":memory:"), clock)
    clock.advance_to(4)
    row = ledger.insert_policy_decision(WF, "k", "P1", False, "why")
    assert ledger.decisions_for(WF) == [row]
    assert (row.id, row.vt, row.allowed) == (1, 4, False)
    assert ledger.decisions_for("other") == []


# ---- executor -----------------------------------------------------------


def test_deny_makes_no_provider_call_and_marks_failed() -> None:
    env = Env(fraud=True)
    result = env.label()
    assert (result.kind, result.error) == ("denied", "policy_denied:P6")
    assert env.carrier.calls == 0
    row = env.row("create_label")
    assert row.status == "FAILED" and row.attempts == 0
    assert row.response == {"error": "policy_denied", "rule_id": "P6"}
    assert row.policy_decision_id == env.ledger.decisions_for(WF)[-1].id


def test_allow_sets_the_decision_id_and_calls_the_provider() -> None:
    env = Env()
    assert env.label().kind == "confirmed"
    assert env.carrier.calls == 1
    row = env.row("create_label")
    assert row.policy_decision_id == env.ledger.decisions_for(WF)[-1].id


def test_dedup_path_is_not_evaluated() -> None:
    env = Env()
    env.label()
    before = len(env.ledger.decisions_for(WF))
    assert env.label().kind == "confirmed"
    assert len(env.ledger.decisions_for(WF)) == before
    assert env.carrier.calls == 1


def test_reconcile_confirmed_path_is_not_evaluated() -> None:
    env = Env(reconciler=lambda row: _confirm(env, row))
    env.label()
    env.ledger.insert_intended(
        WF, "issue_refund", idempotency_key(WF, "issue_refund"), {}
    )
    key = idempotency_key(WF, "issue_refund")
    env.ledger.mark_executing(key)
    env.ledger.mark_unknown(key)
    before = len(env.ledger.decisions_for(WF))
    assert env.refund().kind == "confirmed"
    assert len(env.ledger.decisions_for(WF)) == before


def _confirm(env: Env, row: Any) -> str:
    env.ledger.mark_confirmed(row.idempotency_key, {"refund_id": "r", "amount": 1})
    return "confirmed"


def test_retry_after_not_applied_is_evaluated_by_policy() -> None:
    env = Env(reconciler=lambda row: _fail(env, row))
    env.label()
    env.ledger.set_scan_received(WF)
    key = idempotency_key(WF, "issue_refund")
    env.ledger.insert_intended(WF, "issue_refund", key, {"order_id": "o1"})
    env.ledger.mark_executing(key)
    env.ledger.mark_unknown(key)
    before = len(env.ledger.decisions_for(WF))
    assert env.refund().kind == "confirmed"
    assert len(env.ledger.decisions_for(WF)) == before + 3
    assert env.refunds.calls == 1


def _fail(env: Env, row: Any) -> str:
    env.ledger.mark_failed(row.idempotency_key, {"error": "not_applied"})
    return "failed"


def test_p3_deny_then_allow_after_scan_reuses_the_row() -> None:
    env = Env()
    env.label()
    assert env.refund().error == "policy_denied:P3"
    assert env.refunds.calls == 0
    env.ledger.set_scan_received(WF)
    assert env.refund().kind == "confirmed"
    assert env.row("issue_refund").attempts == 1


def test_escalation_leaves_the_row_intended_without_a_call() -> None:
    env = Env(big=True)
    env.label()
    env.ledger.set_scan_received(WF)
    result = env.refund(45000)
    assert (result.kind, result.error) == ("escalated", "escalated")
    assert env.refunds.calls == 0
    row = env.row("issue_refund")
    assert (row.status, row.attempts, row.policy_decision_id) == ("INTENDED", 0, None)
    assert env.ledger.decisions_for(WF)[-1].rule_id == "P2"
    env.approvals.request(WF, 45000)
    env.approvals.record(WF, True)
    assert env.refund(45000).kind == "confirmed"
    assert env.refunds.calls == 1


# ---- workflow -----------------------------------------------------------


def machine(state: str) -> Workflow:
    clock = Clock()
    ledger = Ledger(sqlite3.connect(":memory:"), clock)
    ledger.upsert_workflow(WF, state, None)
    return Workflow(ledger, WF, TraceRecorder(), clock)


def test_new_edges_are_legal() -> None:
    for state in ("LABEL_PENDING", "REFUND_PENDING", "ESCALATED"):
        flow = machine(state)
        flow.deny("P1")
        assert (flow.state, flow.outcome, flow.is_terminal) == (
            "DENIED",
            "denied_P1",
            True,
        )
    flow = machine("REFUND_PENDING")
    flow.escalate()
    assert flow.state == "ESCALATED" and not flow.is_terminal
    flow.resume_from_escalation()
    assert flow.state == "REFUND_PENDING"
    flow.back_to_awaiting()
    assert flow.state == "AWAITING_RETURN_SCAN"


@pytest.mark.parametrize(
    "state",
    [
        "RECEIVED",
        "ELIGIBILITY_CHECKED",
        "LABEL_CREATED",
        "AWAITING_RETURN_SCAN",
        "CLOSED",
    ],
)
def test_other_edges_raise(state: str) -> None:
    attempts = (
        lambda flow: flow.deny("P1"),
        lambda flow: flow.escalate(),
        lambda flow: flow.resume_from_escalation(),
        lambda flow: flow.back_to_awaiting(),
    )
    for attempt in attempts:
        flow = machine(state)
        with pytest.raises(WorkflowError):
            attempt(flow)


def test_escalated_replays_earlier_targets_but_cannot_advance() -> None:
    flow = machine("ESCALATED")
    flow.advance_to("LABEL_PENDING")
    flow.advance_to("REFUND_PENDING")
    assert flow.state == "ESCALATED"
    with pytest.raises(WorkflowError):
        flow.advance_to("REFUND_CONFIRMED")


def test_denied_is_terminal_and_cannot_advance() -> None:
    flow = machine("DENIED")
    assert flow.is_terminal
    with pytest.raises(WorkflowError):
        flow.advance_to("CLOSED")
    with pytest.raises(WorkflowError):
        flow.fail("x")


def test_constructor_is_all_or_nothing() -> None:
    env = Env()
    base: dict[str, Any] = {
        "store": Store([order()]),
        "carrier": env.carrier,
        "refunds": env.refunds,
        "bus": EventBus(env.scheduler, env.clock, IdGen(1), env.trace),
        "trace": env.trace,
        "clock": env.clock,
        "ledger": env.ledger,
        "max_attempts": 5,
    }
    agent = ScriptedAgent(REQUEST)
    with pytest.raises(ValueError):
        HardenedRuntime(REQUEST, agent, policy_config=POLICY, **base)
    with pytest.raises(ValueError):
        HardenedRuntime(REQUEST, agent, scan_timeout=5, **base)
    HardenedRuntime(REQUEST, agent, **base)


def test_without_policy_config_there_are_no_decisions_and_no_timer() -> None:
    env = Env()
    scheduler = Scheduler(env.clock)
    bus = EventBus(scheduler, env.clock, IdGen(1), env.trace)
    ledger = Ledger(sqlite3.connect(":memory:"), env.clock)
    runtime = HardenedRuntime(
        REQUEST,
        ScriptedAgent(REQUEST),
        store=Store([order()]),
        carrier=SpyCarrier(env.clock, IdGen(1), env.trace, bus),
        refunds=SpyRefunds(env.clock, IdGen(1), env.trace, bus),
        bus=bus,
        trace=env.trace,
        clock=env.clock,
        ledger=ledger,
        max_attempts=5,
    )
    runtime.start()
    scheduler.run_until_idle(100)
    assert ledger.decisions_for(WF) == []
    row = ledger.get_workflow(WF)
    assert row is not None and row.state == "AWAITING_RETURN_SCAN"


def timed_world(scan_timeout: int = 100) -> tuple[Any, ...]:
    clock = Clock()
    scheduler = Scheduler(clock)
    trace = TraceRecorder()
    ids = IdGen(0)
    bus = EventBus(scheduler, clock, ids, trace)
    ledger = Ledger(sqlite3.connect(":memory:"), clock)
    approvals = ApprovalLog()
    approver = SimulatedApprover(
        Rng(0), POLICY.approver, approvals, scheduler, clock, trace, lambda wf: None
    )

    def build() -> HardenedRuntime:
        return HardenedRuntime(
            REQUEST,
            ScriptedAgent(REQUEST),
            store=Store([order()]),
            carrier=CarrierA(clock, ids, trace, bus),
            refunds=RefundService(clock, ids, trace, bus),
            bus=bus,
            trace=trace,
            clock=clock,
            ledger=ledger,
            max_attempts=5,
            policy_config=POLICY,
            approvals=approvals,
            approver=approver,
            scheduler=scheduler,
            scan_timeout=scan_timeout,
        )

    return build, scheduler, ledger, trace, clock, bus


def test_scan_timeout_fires_once_when_no_scan_arrives() -> None:
    build, scheduler, ledger, trace, _, _ = timed_world()
    first = build()
    first.start()
    build().start()
    scheduler.run_until_idle(100)
    row = ledger.get_workflow(WF)
    assert row is not None and (row.state, row.outcome) == (
        "FAILED_NEEDS_REVIEW",
        "scan_timeout",
    )
    terminal = [e for e in trace.events if e.event == "runtime.terminal"]
    assert [e.data["outcome"] for e in terminal] == ["scan_timeout"]


def test_scan_timeout_is_due_at_requested_at_plus_timeout() -> None:
    build, scheduler, _, trace, clock, _ = timed_world()
    build().start()
    scheduler.run_until_idle(100)
    assert clock.now == REQUEST.requested_at + 100


def test_scan_timeout_is_a_no_op_after_a_scan() -> None:
    build, scheduler, ledger, trace, _, bus = timed_world(scan_timeout=100_000)
    build().start()
    bus.publish("return.scanned", WF, {})
    scheduler.run_until_idle(100)
    row = ledger.get_workflow(WF)
    assert row is not None and row.outcome == "closed"
    assert not [e for e in trace.events if e.data.get("outcome") == "scan_timeout"]


def test_stale_timer_from_a_restart_is_harmless() -> None:
    build, scheduler, ledger, trace, _, bus = timed_world(scan_timeout=100_000)
    build().start()
    bus.clear_subscribers()
    build().start()
    bus.publish("return.scanned", WF, {})
    scheduler.run_until_idle(100)
    row = ledger.get_workflow(WF)
    assert row is not None and row.outcome == "closed"
    assert not [e for e in trace.events if e.data.get("outcome") == "scan_timeout"]


# ---- agent --------------------------------------------------------------


def result(error: str | None) -> ToolResult:
    return ToolResult(ok=error is None, data={}, error=error)


def drive(agent: ScriptedAgent, *, early: bool = False) -> ToolCall:
    """Walk an agent to its first refund proposal."""
    obs = Observation(
        last_call=None, last_result=None, timed_out=False, scan_received=False
    )
    action = agent.next_action(obs)
    while isinstance(action, ToolCall) and action.tool != "issue_refund":
        data: dict[str, Any] = {}
        if action.tool == "get_order":
            data = {"order": order().model_dump(mode="json")}
        obs = Observation(
            last_call=action,
            last_result=ToolResult(ok=True, data=data),
            timed_out=False,
            scan_received=False,
        )
        action = agent.next_action(obs)
        if isinstance(action, Wait):
            obs = obs.model_copy(update={"scan_received": True})
            action = agent.next_action(obs)
    assert isinstance(action, ToolCall)
    return action


def obs_for(call: ToolCall, error: str | None, **kw: bool) -> Observation:
    return Observation(
        last_call=call,
        last_result=result(error),
        timed_out=False,
        scan_received=kw.get("scan", True),
        approval_decided=kw.get("approved", False),
    )


def test_agent_waits_after_p3_and_refunds_after_the_scan() -> None:
    agent = ScriptedAgent(REQUEST, early_refund=True)
    refund = drive(agent)
    assert refund.tool == "issue_refund"
    waiting = agent.next_action(obs_for(refund, "policy_denied:P3", scan=False))
    assert isinstance(waiting, Wait)
    assert agent.next_action(obs_for(refund, "policy_denied:P3", scan=False)) == Wait()
    assert agent.next_action(obs_for(refund, "policy_denied:P3", scan=True)) == refund


def test_agent_waits_when_escalated_and_reproposes_on_approval() -> None:
    agent = ScriptedAgent(REQUEST)
    refund = drive(agent)
    assert isinstance(agent.next_action(obs_for(refund, "escalated")), Wait)
    assert isinstance(agent.next_action(obs_for(refund, "escalated")), Wait)
    assert agent.next_action(obs_for(refund, "escalated", approved=True)) == refund


def test_agent_gives_up_on_any_other_error() -> None:
    agent = ScriptedAgent(REQUEST)
    refund = drive(agent)
    assert isinstance(agent.next_action(obs_for(refund, "boom")), Done)


def test_agent_never_refunds_twice_after_a_success() -> None:
    agent = ScriptedAgent(REQUEST)
    refund = drive(agent)
    close = agent.next_action(obs_for(refund, None))
    assert isinstance(close, ToolCall) and close.tool == "close_case"
    assert isinstance(agent.next_action(obs_for(close, None)), Done)


def test_early_refund_success_then_closes_without_a_second_refund() -> None:
    agent = ScriptedAgent(REQUEST, early_refund=True)
    refund = drive(agent)
    after = agent.next_action(obs_for(refund, None, scan=False))
    assert isinstance(after, ToolCall) and after.tool == "close_case"


def test_early_refund_is_skipped_when_the_scan_already_arrived() -> None:
    agent = ScriptedAgent(REQUEST, early_refund=True)
    obs = Observation(
        last_call=None, last_result=None, timed_out=False, scan_received=True
    )
    tools: list[str] = []
    action = agent.next_action(obs)
    while isinstance(action, ToolCall) and action.tool != "issue_refund":
        tools.append(action.tool)
        data: dict[str, Any] = {}
        if action.tool == "get_order":
            data = {"order": order().model_dump(mode="json")}
        obs = Observation(
            last_call=action,
            last_result=ToolResult(ok=True, data=data),
            timed_out=False,
            scan_received=True,
        )
        action = agent.next_action(obs)
    assert tools == ["get_order", "check_policy", "create_return_label"]
    assert isinstance(action, ToolCall) and action.tool == "issue_refund"
    after = agent.next_action(obs_for(action, None))
    assert isinstance(after, ToolCall) and after.tool == "close_case"


def test_agent_checks_last_call_even_when_waiting_for_approval() -> None:
    agent = ScriptedAgent(REQUEST)
    refund = drive(agent)
    agent.next_action(obs_for(refund, "escalated"))
    other = ToolCall(tool="close_case", args={})
    with pytest.raises(AgentError):
        agent.next_action(obs_for(other, "escalated", approved=True))


# ---- end to end ---------------------------------------------------------


def only(
    variant: str, *, early: int = 0, faults: FaultProbabilities = ZERO
) -> ScenarioConfig:
    names = (
        "none",
        "outside_window",
        "high_value_over_cap",
        "fraud_flagged",
        "partial_return",
    )
    weights = VariantWeights(**{n: int(n == variant) for n in names})
    cut2 = CFG.cut2.model_copy(
        update={"variant_weights": weights, "early_refund_percent": early}
    )
    return CFG.model_copy(update={"cut2": cut2, "faults": faults})


def events(res: RunResult) -> list[dict[str, Any]]:
    return [json.loads(line) for line in res.trace_bytes.decode().splitlines()]


def names(res: RunResult) -> list[str]:
    return [e["event"] for e in events(res)]


def refunds(res: RunResult) -> list[int]:
    return [e["data"]["amount"] for e in events(res) if e["event"] == "refund.issued"]


def test_outside_window_is_denied_p4_without_a_label() -> None:
    for seed in range(1, 15):
        res = run_seed(seed, "hardened", only("outside_window"))
        assert res.outcome == "denied_P4"
        assert "label.created_record" not in names(res)
        assert res.violations == []


def test_fraud_is_denied_p6() -> None:
    for seed in range(1, 15):
        res = run_seed(seed, "hardened", only("fraud_flagged"))
        assert res.outcome == "denied_P6"
        assert refunds(res) == []


def test_high_value_resolves_both_ways() -> None:
    outcomes: dict[str, RunResult] = {}
    for seed in range(1, 40):
        res = run_seed(seed, "hardened", only("high_value_over_cap"))
        if res.outcome in ("closed", "denied_P2"):
            outcomes.setdefault(res.outcome, res)
    approved, denied = outcomes["closed"], outcomes["denied_P2"]
    assert len(refunds(approved)) == 1 and refunds(approved)[0] > POLICY.refund_cap
    assert refunds(denied) == []
    for res in (approved, denied):
        assert "approval.decided" in names(res)
        assert any(e["actor"] == "human" for e in events(res))
        assert res.violations == []


def test_partial_return_refunds_the_partial_amount() -> None:
    for seed in range(1, 25):
        cfg = only("partial_return")
        scenario = make_scenario(seed, cfg, IdGen(seed))
        expected = sum(
            x.qty * x.unit_price
            for x in scenario.order.lines
            if x.sku in scenario.request.line_skus
        )
        res = run_seed(seed, "hardened", cfg)
        assert refunds(res) == [expected]
        assert expected < scenario.order.paid_amount
        assert res.outcome == "closed"


def test_never_ships_back_ends_as_scan_timeout() -> None:
    cfg = only("none")
    seed = next(
        s
        for s in range(1, 100)
        if make_scenario(s, cfg, IdGen(s)).behavior == "never_ships_back"
    )
    res = run_seed(seed, "hardened", cfg)
    assert res.outcome == "scan_timeout"
    assert refunds(res) == []


def normal_seed(cfg: ScenarioConfig) -> int:
    return next(
        s for s in range(1, 100) if make_scenario(s, cfg, IdGen(s)).behavior == "normal"
    )


def test_early_refund_is_denied_by_p3_then_refunded_after_the_scan() -> None:
    cfg = only("none", early=100)
    res = run_seed(normal_seed(cfg), "hardened", cfg)
    decisions = [e["data"] for e in events(res) if e["event"] == "policy.decision"]
    assert any(d["rule_id"] == "P3" and d["verdict"] == "deny" for d in decisions)
    assert res.outcome == "closed"
    assert len(refunds(res)) == 1
    steps = [
        (e["data"]["from"], e["data"]["to"])
        for e in events(res)
        if e["event"] == "workflow.advance"
    ]
    start = steps.index(("LABEL_CREATED", "AWAITING_RETURN_SCAN"))
    assert steps[start : start + 5] == [
        ("LABEL_CREATED", "AWAITING_RETURN_SCAN"),
        ("AWAITING_RETURN_SCAN", "REFUND_PENDING"),
        ("REFUND_PENDING", "AWAITING_RETURN_SCAN"),
        ("AWAITING_RETURN_SCAN", "REFUND_PENDING"),
        ("REFUND_PENDING", "REFUND_CONFIRMED"),
    ]


def test_naive_refunds_early_without_policy() -> None:
    cfg = only("none", early=100)
    res = run_seed(normal_seed(cfg), "naive", cfg)
    first_refund = names(res).index("refund.issued")
    assert "return.scanned" not in names(res)[:first_refund]


def test_early_refund_is_the_same_agent_for_every_kind() -> None:
    cfg = only("none", early=100)
    seed = normal_seed(cfg)
    for kind in ("naive", "keyed", "hardened"):
        res = run_seed(seed, kind, cfg)  # type: ignore[arg-type]
        assert "scenario.cut2" in names(res)


def build_hardened(seed: int, cfg: ScenarioConfig) -> Any:
    h = runner._build_harness(seed, cfg)  # noqa: SLF001
    runner._add_policy(h, seed, POLICY)  # noqa: SLF001
    runner._add_ledger(h, sqlite3.connect(":memory:"))  # noqa: SLF001
    runner._schedule_starts("hardened", h, cfg)  # noqa: SLF001
    return h


def escalated(h: Any) -> bool:
    row = h.ledger.get_workflow(h.scenario.request.workflow_id)
    return row is not None and row.state == "ESCALATED"


def test_a_crash_while_escalated_resumes() -> None:
    cfg = only("high_value_over_cap")
    resumed = 0
    for seed in range(1, 30):
        h = build_hardened(seed, cfg)
        while not escalated(h) and h.scheduler.run_next():
            pass
        if not escalated(h):
            continue
        runner._restart("hardened", h, cfg)  # noqa: SLF001
        assert h.slots[0].is_terminal is False
        crashes, loop = runner._drain("hardened", h, cfg)  # noqa: SLF001
        assert (crashes, loop) == (0, False)
        res = runner._finish(seed, "hardened", h, 0, False)  # noqa: SLF001
        assert res.outcome in ("closed", "denied_P2")
        assert len(refunds(res)) == (1 if res.outcome == "closed" else 0)
        assert res.violations == []
        assert "workflow.replay" in names(res)
        resumed += 1
    assert resumed >= 10


def test_notify_reaches_only_current_non_terminal_hardened_slots() -> None:
    old = create_autospec(HardenedRuntime, instance=True)
    old.is_terminal = False
    current = create_autospec(HardenedRuntime, instance=True)
    current.is_terminal = False
    done = create_autospec(HardenedRuntime, instance=True)
    done.is_terminal = True
    other = object()
    slots: list[Any] = [old]
    slots[0] = current
    slots.extend([done, other])
    runner._notify_slots(slots, "wf-1")  # noqa: SLF001
    current.on_approval.assert_called_once_with("wf-1")
    done.on_approval.assert_not_called()
    old.on_approval.assert_not_called()


def test_hardened_has_no_violations_across_cut2_seeds_with_faults() -> None:
    for seed in range(1, 41):
        res = run_seed(seed, "hardened", CFG)
        assert res.violations == [], (seed, res.violations)
