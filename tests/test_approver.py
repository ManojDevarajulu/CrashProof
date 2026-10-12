from crashproof.clock import Clock, Scheduler
from crashproof.config import ApproverSettings, load_policy_config
from crashproof.rng import Rng
from crashproof.runtime.policy import ApprovalLog, SimulatedApprover
from crashproof.sim.trace import TraceRecorder

CFG = load_policy_config().approver


class Env:
    def __init__(self, seed: int = 1, cfg: ApproverSettings = CFG) -> None:
        self.clock = Clock()
        self.scheduler = Scheduler(self.clock)
        self.trace = TraceRecorder()
        self.log = ApprovalLog()
        self.notified: list[str] = []
        self.approver = SimulatedApprover(
            Rng(seed),
            cfg,
            self.log,
            self.scheduler,
            self.clock,
            self.trace,
            self.notified.append,
        )

    def outcome(self, wf: str = "wf-1") -> tuple[int, str]:
        self.approver.request(wf, 50000)
        self.scheduler.run_until_idle(10)
        return self.clock.now, self.log.status(wf)


def test_verdict_and_delay_are_identical_across_instances() -> None:
    assert Env(3).outcome() == Env(3).outcome()


def test_other_draws_do_not_change_the_decision() -> None:
    rng = Rng(3)
    for _ in range(7):
        rng.randint(1, 100)
        rng.fork("faults").randint(1, 10)
    clean = Env(3)
    clean.approver.request("wf-1", 1)
    busy = Env(3)
    busy.approver._rng = rng  # noqa: SLF001
    busy.approver.request("wf-1", 1)
    clean.scheduler.run_until_idle(10)
    busy.scheduler.run_until_idle(10)
    assert clean.clock.now == busy.clock.now
    assert clean.log.status("wf-1") == busy.log.status("wf-1")


def test_delay_is_within_bounds_and_both_verdicts_occur() -> None:
    results = [Env(seed).outcome() for seed in range(1, 60)]
    assert all(CFG.delay_min <= vt <= CFG.delay_max for vt, _ in results)
    assert {status for _, status in results} == {"approved", "denied"}


def test_request_is_idempotent_and_schedules_once() -> None:
    env = Env()
    env.approver.request("wf-1", 50000)
    env.approver.request("wf-1", 50000)
    assert env.scheduler.pending == 1
    env.scheduler.run_until_idle(10)
    env.approver.request("wf-1", 50000)
    assert env.scheduler.pending == 0
    assert env.notified == ["wf-1"]


def test_decision_is_scheduled_at_now_plus_delay() -> None:
    env = Env()
    env.clock.advance_to(500)
    env.approver.request("wf-1", 50000)
    assert env.log.status("wf-1") == "pending"
    env.scheduler.run_until_idle(10)
    assert 500 + CFG.delay_min <= env.clock.now <= 500 + CFG.delay_max


def test_decide_writes_log_then_trace_then_notifies() -> None:
    env = Env()
    seen: list[str] = []
    env.approver._notify = lambda wf: seen.append(  # noqa: SLF001
        f"{wf}:{env.log.status(wf)}:{env.trace.events[-1].event}"
    )
    env.approver.request("wf-1", 50000)
    env.scheduler.run_until_idle(10)
    (event,) = env.trace.events
    assert (event.actor, event.event) == ("human", "approval.decided")
    assert seen[0].startswith("wf-1:") and seen[0].endswith(":approval.decided")
    assert "pending" not in seen[0]


def test_workflows_get_independent_streams() -> None:
    env = Env()
    env.approver.request("wf-1", 1)
    env.approver.request("wf-2", 1)
    assert env.scheduler.pending == 2
