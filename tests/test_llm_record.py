import json
from pathlib import Path

import pytest

from crashproof import cli
from crashproof.agent.base import Observation
from crashproof.agent.llm import (
    LLM_MODEL_ID,
    CallBudget,
    Cassette,
    LLMAgent,
    summarize_run,
)
from crashproof.cli import RecordOutcome
from crashproof.config import load_scenario_config
from crashproof.models import ReturnRequest

CONFIG = load_scenario_config()
REQUEST = ReturnRequest(
    workflow_id="wf-1",
    order_id="ord-1",
    line_skus=["s1"],
    reason="changed_mind",
    requested_at=0,
)
FIRST = Observation(
    last_call=None, last_result=None, timed_out=False, scan_received=False
)
DONE = '{"action":"done"}'
WAIT = '{"action":"wait"}'
TOOL = '{"action":"tool","tool":"get_order","args":{"order_id":"ord-1"}}'
SUMMARY_KEYS = [
    "model",
    "model_calls",
    "output_tokens",
    "prompt_tokens",
    "seeds_attempted",
    "seeds_saved",
    "skipped",
]


class FakeClient:
    def __init__(self, output: str) -> None:
        self.output = output

    def generate(self, prompt: str) -> str:
        return self.output


def markers(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    return [m for m in ("GCP_", "project", "Bearer") if m in text]


def record(directory: Path, seeds: int, client: object, budget: CallBudget) -> Path:
    outcome = RecordOutcome()
    cli._record(seeds, CONFIG, directory, client, budget, outcome)  # type: ignore[arg-type]
    return cli._write_record_summary(directory, outcome, budget)


def test_record_summary_contents_and_no_secrets(tmp_path: Path) -> None:
    budget = CallBudget()
    path = record(tmp_path, 2, FakeClient(DONE), budget)
    body = json.loads(path.read_text(encoding="utf-8"))
    assert sorted(body) == SUMMARY_KEYS
    assert body["model"] == LLM_MODEL_ID
    assert body["seeds_attempted"] == [1, 2]
    assert body["seeds_saved"] == [1, 2]
    assert body["skipped"] == []
    assert body["model_calls"] == budget.calls == 0
    assert markers(path) == []


def test_record_summary_lists_skipped_seed_with_reason(tmp_path: Path) -> None:
    budget = CallBudget(1)

    class Capped:
        def generate(self, prompt: str) -> str:
            budget.charge()
            return TOOL

    path = record(tmp_path, 3, Capped(), budget)
    body = json.loads(path.read_text(encoding="utf-8"))
    assert body["seeds_attempted"] == [1]
    assert body["seeds_saved"] == []
    assert body["skipped"] == [{"seed": 1, "reason": "call_cap_reached"}]
    assert body["model_calls"] == 1
    assert markers(path) == []


def test_malformed_run_is_categorised_per_runtime(tmp_path: Path) -> None:
    record(tmp_path, 1, FakeClient("garbage"), CallBudget())
    cassette = Cassette.load(tmp_path / "seed-0001.json")
    assert sorted(cassette.runs) == ["hardened", "keyed", "naive"]
    for run in cassette.runs.values():
        assert run["ended"] == "ended_by_malformed_output"
        assert int(run["steps"]) >= 1
    assert "garbage" in (tmp_path / "seed-0001.json").read_text(encoding="utf-8")


def test_finished_run_is_categorised_finished(tmp_path: Path) -> None:
    record(tmp_path, 1, FakeClient(DONE), CallBudget())
    runs = Cassette.load(tmp_path / "seed-0001.json").runs
    assert {run["ended"] for run in runs.values()} == {"finished"}


def test_step_cap_and_precedence() -> None:
    capped = LLMAgent(REQUEST, Cassette(1), FakeClient(WAIT))
    for _ in range(31):
        capped.next_action(FIRST)
    assert capped.end_reason == "ended_by_step_cap"
    assert summarize_run([capped]) == {"ended": "ended_by_step_cap", "steps": 30}
    bad = LLMAgent(REQUEST, Cassette(2), FakeClient("nope"))
    bad.next_action(FIRST)
    both = summarize_run([capped, bad])
    assert both == {"ended": "ended_by_malformed_output", "steps": 31}
    fine = LLMAgent(REQUEST, Cassette(3), FakeClient(DONE))
    fine.next_action(FIRST)
    assert summarize_run([fine]) == {"ended": "finished", "steps": 1}


def test_eval_reports_ended_counts_and_skipped_per_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    directory = tmp_path / "tests" / "cassettes"
    record(directory, 2, FakeClient("garbage"), CallBudget())
    summary_path = directory / "_record_summary.json"
    body = json.loads(summary_path.read_text(encoding="utf-8"))
    body["skipped"] = [{"seed": 3, "reason": "call_cap_reached"}]
    summary_path.write_text(json.dumps(body), encoding="utf-8")
    cli.main(["eval", "--seeds", "2", "--runtimes", "naive,keyed,hardened"])
    llm = json.loads(Path("results/summary.json").read_text("utf-8"))["llm"]
    assert llm["skipped_seeds"] == [3]
    for name in ("naive", "keyed", "hardened"):
        stats = llm["runtimes"][name]
        assert stats["seeds_recorded"] == 2
        assert stats["seeds_skipped"] == 1
        assert stats["ended_by_malformed_output"] == 2
        assert stats["ended_by_step_cap"] == 0
        assert stats["ended_by_call_budget"] == 0
        assert float(stats["mean_agent_steps"]) >= 1
        assert "I1" in stats
    cli.main(["report"])
    report = Path("results/summary.md").read_text("utf-8")
    assert "### How the recorded runs ended" in report
    assert (
        "One recorded run of one model on a small seed set; not a statistical "
        "estimate." in report
    )
