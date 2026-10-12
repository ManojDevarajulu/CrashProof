import re
import sqlite3
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from crashproof.clock import Clock
from crashproof.config import PolicyConfig, load_policy_config
from crashproof.models import Line, Order, ReturnRequest
from crashproof.runtime.executor import idempotency_key
from crashproof.runtime.ledger import Ledger
from crashproof.runtime.policy import ApprovalLog, PolicyEngine, PolicyError
from crashproof.sim.scenario import ORACLE_WINDOW_DAYS
from crashproof.sim.trace import TraceRecorder
from crashproof.world.store import Store

WF = "wf-1"
DAY = 86400
CFG = load_policy_config()
SRC = Path(__file__).parent.parent / "src" / "crashproof"


def make_order(
    *,
    delivered_at: int | None = 0,
    fraud_risk: bool = False,
    prices: tuple[int, int] = (1500, 700),
) -> Order:
    lines = [
        Line(sku="a", qty=2, unit_price=prices[0]),
        Line(sku="b", qty=1, unit_price=prices[1]),
    ]
    return Order(
        order_id="o1",
        customer_id="c1",
        lines=lines,
        paid_amount=sum(x.qty * x.unit_price for x in lines),
        delivered_at=delivered_at,
        carrier="A",
        fraud_risk=fraud_risk,
    )


class Env:
    def __init__(
        self,
        order: Order | None = None,
        *,
        skus: tuple[str, ...] = ("a", "b"),
        requested_at: int = 0,
        cfg: PolicyConfig = CFG,
    ) -> None:
        self.clock = Clock()
        self.trace = TraceRecorder()
        self.ledger = Ledger(sqlite3.connect(":memory:"), self.clock)
        self.ledger.upsert_workflow(WF, "RECEIVED", None)
        self.approvals = ApprovalLog()
        request = ReturnRequest(
            workflow_id=WF,
            order_id="o1",
            line_skus=list(skus),
            reason="r",
            requested_at=requested_at,
        )
        self.engine = PolicyEngine(
            cfg,
            Store([order or make_order()]),
            request,
            self.ledger,
            self.approvals,
            self.trace,
            self.clock,
        )

    def label(self) -> tuple[str, int]:
        key = idempotency_key(WF, "create_label")
        verdict = self.engine.evaluate("create_label", key, None)
        return verdict.kind, len(self.ledger.decisions_for(WF))

    def refund(self, amount: int) -> str:
        key = idempotency_key(WF, "issue_refund")
        return self.engine.evaluate("issue_refund", key, amount).kind

    def rules(self) -> list[str]:
        return [d.rule_id for d in self.ledger.decisions_for(WF)]


def test_default_rule_order_and_values() -> None:
    assert CFG.label == ["P6", "P4", "P5"]
    assert CFG.refund == ["P3", "P1", "P2"]
    assert (CFG.refund_cap, CFG.window_days) == (20000, 30)
    assert (CFG.approver.delay_min, CFG.approver.delay_max) == (3600, 86400)
    assert (CFG.approver.approve_weight, CFG.approver.deny_weight) == (7, 3)


def test_window_days_equals_the_oracle_constant() -> None:
    assert CFG.window_days == ORACLE_WINDOW_DAYS


def test_p6_allow_and_deny() -> None:
    assert Env().label()[0] == "allow"
    env = Env(make_order(fraud_risk=True))
    assert env.label()[0] == "deny"
    assert env.rules() == ["P6"]


def test_p4_allow_deny_and_boundary() -> None:
    assert Env(requested_at=30 * DAY).label()[0] == "allow"
    env = Env(requested_at=30 * DAY + 1)
    assert env.label()[0] == "deny"
    assert env.rules() == ["P6", "P4"]
    assert Env(make_order(delivered_at=None)).label()[0] == "deny"


def test_p5_allow_and_deny_on_another_confirmed_label() -> None:
    env = Env()
    assert env.label()[0] == "allow"
    env.ledger.insert_intended(WF, "create_label", "other", {})
    env.ledger.mark_executing("other")
    env.ledger.mark_confirmed("other", {"label_id": "l1"})
    assert env.label()[0] == "deny"
    assert env.rules()[-1] == "P5"


def test_p5_ignores_unconfirmed_labels() -> None:
    env = Env()
    env.ledger.insert_intended(WF, "create_label", "other", {})
    assert env.label()[0] == "allow"


def test_p3_allow_and_deny_read_only_the_ledger() -> None:
    env = Env()
    assert env.refund(100) == "deny"
    assert env.rules() == ["P3"]
    env.ledger.set_scan_received(WF)
    assert env.refund(100) == "allow"


def test_p1_allow_and_deny() -> None:
    env = Env(skus=("a",))
    env.ledger.set_scan_received(WF)
    assert env.refund(3000) == "allow"
    assert env.refund(3001) == "deny"
    assert env.rules()[-1] == "P1"


def test_p2_for_each_log_status() -> None:
    env = Env(make_order(prices=(20000, 5000)))
    env.ledger.set_scan_received(WF)
    assert env.approvals.status(WF) == "none"
    assert env.refund(45000) == "escalate"
    env.approvals.request(WF, 45000)
    assert env.refund(45000) == "escalate"
    env.approvals.record(WF, True)
    assert env.refund(45000) == "allow"
    other = Env(make_order(prices=(20000, 5000)))
    other.ledger.set_scan_received(WF)
    other.approvals.request(WF, 45000)
    other.approvals.record(WF, False)
    assert other.refund(45000) == "deny"
    assert other.rules()[-1] == "P2"


def test_p2_at_the_cap_needs_no_approval() -> None:
    env = Env(make_order(prices=(10000, 0)))
    env.ledger.set_scan_received(WF)
    assert env.refund(20000) == "allow"


def test_evaluation_stops_at_the_first_deny() -> None:
    env = Env(make_order(fraud_risk=True), requested_at=100 * DAY)
    assert env.label() == ("deny", 1)
    refund = Env()
    assert refund.refund(10**6) == "deny"
    assert refund.rules() == ["P3"]


def test_one_row_and_one_trace_event_per_rule_evaluated() -> None:
    env = Env()
    env.ledger.set_scan_received(WF)
    assert env.refund(100) == "allow"
    assert env.rules() == ["P3", "P1", "P2"]
    events = [e for e in env.trace.events if e.event == "policy.decision"]
    assert [e.data["rule_id"] for e in events] == ["P3", "P1", "P2"]
    assert all(e.actor == "policy" for e in events)
    rows = env.ledger.decisions_for(WF)
    assert all(r.allowed for r in rows)
    assert [e.data["decision_id"] for e in events] == [r.id for r in rows]


def test_verdict_carries_the_last_decision_id() -> None:
    env = Env()
    key = idempotency_key(WF, "create_label")
    verdict = env.engine.evaluate("create_label", key, None)
    assert verdict.rule_id == "P5"
    assert verdict.decision_id == env.ledger.decisions_for(WF)[-1].id


def test_refund_rules_need_an_amount() -> None:
    env = Env()
    env.ledger.set_scan_received(WF)
    with pytest.raises(PolicyError):
        env.engine.evaluate("issue_refund", "k", None)


def test_approval_log_rules() -> None:
    log = ApprovalLog()
    log.request("w2", 5)
    log.request("w1", 7)
    log.request("w1", 99)
    assert [(r.workflow_id, r.amount) for r in log.records()] == [("w1", 7), ("w2", 5)]
    with pytest.raises(PolicyError):
        log.record("missing", True)
    log.record("w1", False)
    assert log.status("w1") == "denied"
    with pytest.raises(PolicyError):
        log.record("w1", True)


def write(tmp_path: Path, mutate: dict[str, object]) -> Path:
    raw = yaml.safe_load((SRC / "config" / "policy_rules.yaml").read_text("utf-8"))
    raw.update(mutate)
    path = tmp_path / "p.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8", newline="\n")
    return path


def test_policy_loader_custom_path(tmp_path: Path) -> None:
    assert load_policy_config(write(tmp_path, {"refund_cap": 5})).refund_cap == 5


@pytest.mark.parametrize(
    "mutate",
    [
        {"extra": 1},
        {"refund_cap": 0},
        {"window_days": 0},
        {"label": ["P9"]},
        {
            "approver": {
                "delay_min": 5,
                "delay_max": 1,
                "approve_weight": 1,
                "deny_weight": 1,
            }
        },
        {
            "approver": {
                "delay_min": 1,
                "delay_max": 2,
                "approve_weight": 0,
                "deny_weight": 0,
            }
        },
    ],
)
def test_policy_loader_rejects_bad_values(
    tmp_path: Path, mutate: dict[str, object]
) -> None:
    with pytest.raises(ValidationError):
        load_policy_config(write(tmp_path, mutate))


def test_runtime_and_agent_never_import_the_scenario_or_mention_the_oracle() -> None:
    files = sorted((SRC / "runtime").glob("*.py")) + sorted(
        (SRC / "agent").glob("*.py")
    )
    assert files
    for path in files:
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"oracle", text, re.IGNORECASE), path
        assert "sim.scenario" not in text, path
