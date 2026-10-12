import sqlite3
from collections.abc import Callable
from contextlib import ExitStack, closing
from dataclasses import dataclass, field
from functools import partial
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict

from crashproof.agent.base import Agent
from crashproof.agent.scripted import ScriptedAgent
from crashproof.clock import Clock, Scheduler
from crashproof.config import PolicyConfig, ScenarioConfig, load_policy_config
from crashproof.ids import IdGen
from crashproof.models import Json
from crashproof.rng import Rng
from crashproof.runtime.executor import Reconciler
from crashproof.runtime.keyed import KeyedRuntime
from crashproof.runtime.ledger import Ledger
from crashproof.runtime.naive import NaiveRuntime
from crashproof.runtime.policy import ApprovalLog, SimulatedApprover
from crashproof.runtime.reconcile import make_reconciler, reconcile_workflow
from crashproof.runtime.workflow import TERMINAL, HardenedRuntime
from crashproof.sim.invariants import Violation, check_i9, check_world
from crashproof.sim.scenario import Scenario, make_scenario
from crashproof.sim.trace import TraceRecorder, serialize, trace_hash
from crashproof.world.carriers import CarrierA, CarrierB, CarrierC, LabelProvider
from crashproof.world.events import EventBus, WorldEvent
from crashproof.world.faults import FaultInjector, SimulatedCrash
from crashproof.world.refunds import RefundService
from crashproof.world.store import Store

QUIESCENCE_SECONDS = 72 * 3600

type RuntimeKind = Literal["naive", "keyed", "hardened"]
type AgentFactory = Callable[[Scenario], Agent]


class _Runtime(Protocol):
    @property
    def is_terminal(self) -> bool: ...

    @property
    def outcome(self) -> str | None: ...

    def start(self) -> None: ...


class RunResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    seed: int
    runtime: RuntimeKind
    trace_hash: str
    trace_bytes: bytes
    violations: list[Violation]
    restarts: int
    outcome: str


class _Customer:
    """Harness-owned subscriber. It survives restarts and ships back at most once."""

    def __init__(
        self,
        scenario: Scenario,
        bus: EventBus,
        trace: TraceRecorder,
        clock: Clock,
        c_scan_extra_delay: int,
    ) -> None:
        self._scenario = scenario
        self._c_scan_extra_delay = c_scan_extra_delay
        self._bus = bus
        self._trace = trace
        self._clock = clock
        self._shipped = False

    def on_event(self, event: WorldEvent) -> None:
        workflow_id = self._scenario.request.workflow_id
        if event.type != "label.created" or event.workflow_id != workflow_id:
            return
        if self._shipped:
            return
        self._shipped = True
        if self._scenario.behavior == "never_ships_back":
            return
        data: dict[str, Json] = {"label_id": event.data["label_id"]}
        self._trace.record(
            self._clock.now, workflow_id, "world", "customer.ships", data
        )
        if self._scenario.carrier == "C":
            delay = self._scenario.scan_delay + self._c_scan_extra_delay
            for _ in range(2):
                self._bus.publish("return.scanned", workflow_id, data, delay=delay)
            return
        self._bus.publish(
            "return.scanned", workflow_id, data, delay=self._scenario.scan_delay
        )


def scripted_agent(scenario: Scenario) -> Agent:
    return ScriptedAgent(scenario.request, early_refund=scenario.early_refund)


@dataclass
class _Harness:
    clock: Clock
    scheduler: Scheduler
    trace: TraceRecorder
    scenario: Scenario
    faults: FaultInjector
    bus: EventBus
    store: Store
    carrier: LabelProvider
    refunds: RefundService
    customer: _Customer
    slots: list[_Runtime] = field(default_factory=list)
    ledger: Ledger | None = None
    reconciler: Reconciler | None = None
    policy_config: PolicyConfig | None = None
    approvals: ApprovalLog | None = None
    approver: SimulatedApprover | None = None
    agent_factory: AgentFactory = scripted_agent


def _make_carrier(
    scenario: Scenario,
    config: ScenarioConfig,
    clock: Clock,
    ids: IdGen,
    trace: TraceRecorder,
    bus: EventBus,
    faults: FaultInjector,
) -> LabelProvider:
    match scenario.carrier:
        case "A":
            return CarrierA(clock, ids, trace, bus, faults)
        case "B":
            return CarrierB(
                clock,
                ids,
                trace,
                bus,
                faults,
                label_delay=config.carriers.b_label_delay,
            )
        case "C":
            return CarrierC(clock, ids, trace, bus, faults)


def _build_harness(seed: int, config: ScenarioConfig) -> _Harness:
    clock = Clock()
    scheduler = Scheduler(clock)
    rng = Rng(seed)
    ids = IdGen(seed)
    trace = TraceRecorder()
    scenario = make_scenario(seed, config, ids)
    faults = FaultInjector(rng, config.faults, clock, trace)
    bus = EventBus(scheduler, clock, ids, trace, faults)
    store = Store([scenario.order])
    carrier = _make_carrier(scenario, config, clock, ids, trace, bus, faults)
    refunds = RefundService(clock, ids, trace, bus, faults)
    customer = _Customer(
        scenario, bus, trace, clock, config.carriers.c_scan_extra_delay
    )
    bus.subscribe(customer.on_event)
    return _Harness(
        clock=clock,
        scheduler=scheduler,
        trace=trace,
        scenario=scenario,
        faults=faults,
        bus=bus,
        store=store,
        carrier=carrier,
        refunds=refunds,
        customer=customer,
    )


def _add_ledger(h: _Harness, conn: sqlite3.Connection) -> None:
    h.ledger = Ledger(conn, h.clock)
    h.reconciler = make_reconciler(h.ledger, h.carrier, h.refunds, h.trace, h.clock)


def _notify_slots(slots: list[_Runtime], workflow_id: str) -> None:
    for slot in list(slots):
        if isinstance(slot, HardenedRuntime) and not slot.is_terminal:
            slot.on_approval(workflow_id)


def _add_policy(h: _Harness, seed: int, policy_config: PolicyConfig) -> None:
    h.policy_config = policy_config
    h.approvals = ApprovalLog()
    h.approver = SimulatedApprover(
        Rng(seed),
        policy_config.approver,
        h.approvals,
        h.scheduler,
        h.clock,
        h.trace,
        partial(_notify_slots, h.slots),
    )


def _make_runtime(kind: RuntimeKind, h: _Harness, config: ScenarioConfig) -> _Runtime:
    agent = h.agent_factory(h.scenario)
    if kind in ("naive", "keyed"):
        runtime_class = NaiveRuntime if kind == "naive" else KeyedRuntime
        return runtime_class(
            h.scenario.request,
            agent,
            store=h.store,
            carrier=h.carrier,
            refunds=h.refunds,
            bus=h.bus,
            trace=h.trace,
            clock=h.clock,
            faults=h.faults,
        )
    if h.ledger is None or h.policy_config is None:
        raise RuntimeError("hardened runtime needs a ledger and a policy config")
    return HardenedRuntime(
        h.scenario.request,
        agent,
        store=h.store,
        carrier=h.carrier,
        refunds=h.refunds,
        bus=h.bus,
        trace=h.trace,
        clock=h.clock,
        ledger=h.ledger,
        max_attempts=config.max_attempts,
        faults=h.faults,
        reconciler=h.reconciler,
        policy_config=h.policy_config,
        approvals=h.approvals,
        approver=h.approver,
        scheduler=h.scheduler,
        scan_timeout=config.timing.scan_timeout,
    )


def _start_slot(kind: RuntimeKind, h: _Harness, config: ScenarioConfig) -> None:
    runtime = _make_runtime(kind, h, config)
    h.slots.append(runtime)
    runtime.start()


def _schedule_starts(kind: RuntimeKind, h: _Harness, config: ScenarioConfig) -> None:
    def start() -> None:
        _start_slot(kind, h, config)

    h.scheduler.schedule_at(0, start)
    if h.scenario.behavior == "duplicate_request":
        h.scheduler.schedule_at(config.timing.duplicate_request_delay, start)


def _restart(kind: RuntimeKind, h: _Harness, config: ScenarioConfig) -> None:
    """Rebuild every runtime that had not terminated; a finished one has no process."""
    h.bus.clear_subscribers()
    h.bus.subscribe(h.customer.on_event)
    for index, slot in enumerate(h.slots):
        if slot.is_terminal:
            continue
        rebuilt = _make_runtime(kind, h, config)
        h.slots[index] = rebuilt
        rebuilt.start()


def _record(h: _Harness, event: str, data: dict[str, Json]) -> None:
    workflow_id = h.scenario.request.workflow_id
    h.trace.record(h.clock.now, workflow_id, "runtime", event, data)


def _drain(kind: RuntimeKind, h: _Harness, config: ScenarioConfig) -> tuple[int, bool]:
    """Run to idle, restarting after each crash. Returns (crashes, crash_loop)."""
    crashes = 0
    restart = False
    while True:
        try:
            if restart:
                _restart(kind, h, config)
                restart = False
            h.scheduler.run_until_idle(config.run.max_events)
            return crashes, False
        except SimulatedCrash as crash:
            crashes += 1
            if crashes > config.run.max_restarts:
                _record(h, "runner.crash_loop", {"crashes": crashes})
                return crashes, True
            _record(h, "runner.restart", {"crash": crashes, "point": crash.point})
            restart = True


def _quiesce(
    kind: RuntimeKind,
    h: _Harness,
    config: ScenarioConfig,
    crashes: int,
    crash_loop: bool,
) -> tuple[int, bool]:
    """Reconcile (hardened), advance 72 virtual hours with a silent no-op, drain."""
    if h.ledger is not None and h.reconciler is not None:
        reconcile_workflow(h.scenario.request.workflow_id, h.ledger, h.reconciler)
    h.scheduler.schedule_at(h.clock.now + QUIESCENCE_SECONDS, _noop)
    more, looped = _drain(kind, h, config)
    return crashes + more, crash_loop or looped


def _noop() -> None:
    return None


def _workflow_terminal(h: _Harness) -> dict[str, bool]:
    workflow_id = h.scenario.request.workflow_id
    if h.ledger is not None:
        row = h.ledger.get_workflow(workflow_id)
        return {workflow_id: row is not None and row.state in TERMINAL}
    return {workflow_id: all(slot.is_terminal for slot in h.slots)}


def run_seed(
    seed: int,
    kind: RuntimeKind,
    config: ScenarioConfig,
    policy_config: PolicyConfig | None = None,
    *,
    agent_factory: AgentFactory = scripted_agent,
) -> RunResult:
    with ExitStack() as stack:
        h = _build_harness(seed, config)
        h.agent_factory = agent_factory
        _add_policy(
            h, seed, load_policy_config() if policy_config is None else policy_config
        )
        if kind == "hardened":
            conn = stack.enter_context(closing(sqlite3.connect(":memory:")))
            _add_ledger(h, conn)
        scenario = h.scenario
        h.trace.record(
            h.clock.now,
            scenario.request.workflow_id,
            "world",
            "scenario.created",
            {
                "behavior": scenario.behavior,
                "paid_amount": scenario.order.paid_amount,
                "scan_delay": scenario.scan_delay,
            },
        )
        if scenario.carrier != "A":
            h.trace.record(
                h.clock.now,
                scenario.request.workflow_id,
                "world",
                "scenario.carrier",
                {"carrier": scenario.carrier},
            )
        if scenario.variant != "none" or scenario.early_refund:
            h.trace.record(
                h.clock.now,
                scenario.request.workflow_id,
                "world",
                "scenario.cut2",
                {
                    "variant": scenario.variant,
                    "early_refund": scenario.early_refund,
                    "oracle_eligible": scenario.oracle_eligible,
                },
            )
        _schedule_starts(kind, h, config)
        crashes, crash_loop = _drain(kind, h, config)
        crashes, crash_loop = _quiesce(kind, h, config, crashes, crash_loop)
        return _finish(seed, kind, h, crashes, crash_loop)


def _finish(
    seed: int, kind: RuntimeKind, h: _Harness, crashes: int, crash_loop: bool
) -> RunResult:
    effects = None
    if h.ledger is not None and h.reconciler is not None:
        reconcile_workflow(h.scenario.request.workflow_id, h.ledger, h.reconciler)
        effects = h.ledger.all_effects()
    decisions = None
    if h.ledger is not None:
        workflow_id = h.scenario.request.workflow_id
        decisions = {workflow_id: h.ledger.decisions_for(workflow_id)}
    violations = check_world(
        [h.scenario.order],
        h.refunds.refunds,
        h.carrier.labels,
        effects,
        trace_events=h.trace.events,
        oracle_eligible=h.scenario.oracle_eligible,
        approvals=None if h.approvals is None else h.approvals.records(),
        refund_cap=None if h.policy_config is None else h.policy_config.refund_cap,
        decisions_by_workflow=decisions,
        workflow_terminal=_workflow_terminal(h),
    )
    events = h.trace.events
    return RunResult(
        seed=seed,
        runtime=kind,
        trace_hash=trace_hash(events),
        trace_bytes=serialize(events),
        violations=violations,
        restarts=crashes,
        outcome=_outcome(h, crash_loop),
    )


def _outcome(h: _Harness, crash_loop: bool) -> str:
    if crash_loop:
        return "crash_loop"
    if h.slots and h.slots[0].outcome is not None:
        return h.slots[0].outcome
    return "open"


def _run_once(
    seed: int,
    kind: RuntimeKind,
    config: ScenarioConfig,
    policy_config: PolicyConfig | None,
    agent_factory: AgentFactory,
) -> RunResult:
    if agent_factory is not scripted_agent:
        return run_seed(seed, kind, config, policy_config, agent_factory=agent_factory)
    if policy_config is None:
        return run_seed(seed, kind, config)
    return run_seed(seed, kind, config, policy_config)


def evaluate_seed(
    seed: int,
    kind: RuntimeKind,
    config: ScenarioConfig,
    policy_config: PolicyConfig | None = None,
    *,
    agent_factory: AgentFactory = scripted_agent,
) -> RunResult:
    """Run a seed twice on fresh harnesses and add an I9 violation if they differ."""
    first = _run_once(seed, kind, config, policy_config, agent_factory)
    second = _run_once(seed, kind, config, policy_config, agent_factory)
    extra = check_i9(first.trace_hash, second.trace_hash)
    if not extra:
        return first
    return first.model_copy(update={"violations": first.violations + extra})
