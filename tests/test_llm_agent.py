import ast
import json
from pathlib import Path

import pytest

from crashproof import cli
from crashproof.agent import llm
from crashproof.agent.base import Done, Observation, Wait
from crashproof.agent.llm import (
    CallBudget,
    CallCapReached,
    Cassette,
    CassetteMiss,
    GeminiClient,
    LLMAgent,
    ModelCallError,
    build_prompt,
)
from crashproof.config import load_scenario_config
from crashproof.models import ReturnRequest
from crashproof.runtime.tools import ToolCall
from crashproof.sim.runner import RuntimeKind, run_seed

ROOT = Path(__file__).resolve().parent.parent
CASSETTES = ROOT / "tests" / "cassettes"
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
TOOL = '{"action":"tool","tool":"get_order","args":{"order_id":"ord-1"}}'
POLICY = '{"action":"tool","tool":"check_policy","args":{"order_id":"ord-1"}}'
DONE = '{"action":"done"}'


class FakeClient:
    def __init__(self, outputs: list[str]) -> None:
        self.outputs = outputs
        self.prompts: list[str] = []

    def generate(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.outputs[min(len(self.prompts), len(self.outputs)) - 1]


def prompts_for(kind: RuntimeKind) -> list[str]:
    client = FakeClient([TOOL, POLICY, DONE])
    cassette = Cassette(4)
    run_seed(
        4,
        kind,
        CONFIG,
        agent_factory=lambda s: LLMAgent(s.request, cassette, client),
    )
    return client.prompts


def test_prompt_identical_under_all_runtimes() -> None:
    naive = prompts_for("naive")
    assert len(naive) == 3
    assert [p.encode() for p in naive] == [p.encode() for p in prompts_for("keyed")]
    assert [p.encode() for p in naive] == [p.encode() for p in prompts_for("hardened")]


def test_prompt_never_mentions_internals() -> None:
    text = build_prompt(REQUEST, ["called get_order {} -> error x"], FIRST).lower()
    for word in ("idempot", "ledger", "naive", "keyed", "hardened", "runtime", "key"):
        assert word not in text
    for word in ("gcp_", "project", "bearer"):
        assert word not in text


def test_replay_matches_recording_and_miss_raises() -> None:
    cassette = Cassette(1)
    live = LLMAgent(REQUEST, cassette, FakeClient([TOOL]))
    action = live.next_action(FIRST)
    assert action == ToolCall(tool="get_order", args={"order_id": "ord-1"})
    replayed = LLMAgent(REQUEST, Cassette.load_from_text(cassette.to_json()), None)
    assert replayed.next_action(FIRST) == action
    with pytest.raises(CassetteMiss):
        LLMAgent(REQUEST, Cassette(1), None).next_action(FIRST)


def test_malformed_output_is_failed_step_and_stored() -> None:
    cassette = Cassette(1)
    agent = LLMAgent(REQUEST, cassette, FakeClient(["not json {"]))
    assert agent.next_action(FIRST) == Done()
    assert agent.failed_steps == [0]
    assert "not json {" in cassette.to_json()
    assert agent.next_action(FIRST) == Done()
    again = LLMAgent(REQUEST, cassette, None)
    assert again.next_action(FIRST) == Done()
    assert again.failed_steps == [0]


def test_unknown_tool_is_failed_step() -> None:
    bad = '{"action":"tool","tool":"drop_db","args":{}}'
    agent = LLMAgent(REQUEST, Cassette(1), FakeClient([bad]))
    assert agent.next_action(FIRST) == Done()
    assert agent.failed_steps == [0]


def test_wait_action_and_step_limit() -> None:
    agent = LLMAgent(REQUEST, Cassette(1), FakeClient(['{"action":"wait"}']))
    assert agent.next_action(FIRST) == Wait()
    looping = LLMAgent(REQUEST, Cassette(1), FakeClient(['{"action":"wait"}']))
    actions = [looping.next_action(FIRST) for _ in range(llm.MAX_AGENT_STEPS + 1)]
    assert actions[-1] == Done()
    assert looping.failed_steps


def test_budget_cap() -> None:
    budget = CallBudget(2)
    budget.charge()
    budget.charge()
    with pytest.raises(CallCapReached):
        budget.charge()


def test_record_rejects_more_than_50_seeds() -> None:
    with pytest.raises(SystemExit):
        cli.main(["record", "--seeds", "51"])


def test_gemini_client_env_and_no_leak() -> None:
    with pytest.raises(ModelCallError, match="GCP_PROJECT"):
        GeminiClient(CallBudget(), {})
    env = {"GCP_PROJECT": "secret-p", "GCP_LOCATION": "l", "GEMINI_MODEL": "other"}
    with pytest.raises(ModelCallError) as info:
        GeminiClient(CallBudget(), env)
    assert "secret-p" not in str(info.value)


def test_gemini_client_with_fake_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    class Usage:
        prompt_token_count = 5
        candidates_token_count = 2

    class Response:
        usage_metadata = Usage()
        text = DONE

    class Models:
        def generate_content(self, **kwargs: object) -> Response:
            if kwargs["contents"] == "boom":
                raise RuntimeError("project secret-p exploded")
            return Response()

    class Client:
        def __init__(self, **kwargs: object) -> None:
            self.models = Models()

    class Genai:
        pass

    Genai.Client = Client  # type: ignore[attr-defined]

    class Types:
        @staticmethod
        def GenerateContentConfig(**kwargs: object) -> object:
            return kwargs

    monkeypatch.setattr(llm, "_load_sdk", lambda: (Genai, Types))
    env = {
        "GCP_PROJECT": "secret-p",
        "GCP_LOCATION": "l",
        "GEMINI_MODEL": llm.LLM_MODEL_ID,
    }
    budget = CallBudget()
    client = GeminiClient(budget, env)
    assert client.generate("hi") == DONE
    assert (budget.calls, budget.prompt_tokens, budget.output_tokens) == (1, 5, 2)
    with pytest.raises(ModelCallError) as info:
        client.generate("boom")
    assert "secret-p" not in str(info.value)


def test_llm_module_has_no_top_level_sdk_import() -> None:
    tree = ast.parse((ROOT / "src/crashproof/agent/llm.py").read_text("utf-8"))
    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            assert not (node.module or "").startswith("google")
        if isinstance(node, ast.Import):
            assert not any(a.name.startswith("google") for a in node.names)


def find_secret_markers(directory: Path) -> list[str]:
    hits: list[str] = []
    for path in sorted(directory.rglob("*")):
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        hits += [
            f"{path.name}: {m}" for m in ("GCP_", "project", "Bearer") if m in text
        ]
    return hits


def test_committed_cassettes_hold_no_secrets() -> None:
    assert find_secret_markers(CASSETTES) == []


def test_secret_scan_detects_markers(tmp_path: Path) -> None:
    (tmp_path / "a.json").write_text("Bearer abc", encoding="utf-8")
    assert find_secret_markers(tmp_path)


def test_record_then_eval_adds_separate_llm_section(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    directory = tmp_path / "tests" / "cassettes"
    code = cli._record(2, CONFIG, directory, FakeClient([DONE]), CallBudget())
    assert code == 0
    assert len(list(directory.glob("seed-*.json"))) == 2
    cli.main(["eval", "--seeds", "3", "--runtimes", "naive,keyed,hardened"])
    summary = json.loads(Path("results/summary.json").read_text("utf-8"))
    assert summary["llm"]["seeds"] == [1, 2]
    assert sorted(summary["llm"]["runtimes"]) == ["hardened", "keyed", "naive"]
    cli.main(["report"])
    report = Path("results/summary.md").read_text("utf-8")
    assert "## LLM agent (recorded cassettes, 2 seeds)" in report


def test_record_stops_at_cap_and_discards_seed(tmp_path: Path) -> None:
    budget = CallBudget(1)

    class Capped:
        def generate(self, prompt: str) -> str:
            budget.charge()
            return TOOL

    assert cli._record(1, CONFIG, tmp_path, Capped(), budget) == 1
    assert list(tmp_path.glob("*.json")) == []


def test_eval_without_cassettes_has_no_llm_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    cli.main(["eval", "--seeds", "2", "--runtimes", "hardened"])
    assert "llm" not in json.loads(Path("results/summary.json").read_text("utf-8"))
