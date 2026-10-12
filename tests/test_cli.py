import json
from pathlib import Path

import pytest

from crashproof import cli
from crashproof.cli import main
from crashproof.sim.invariants import Violation
from crashproof.sim.runner import RunResult, RuntimeKind


@pytest.fixture(autouse=True)
def in_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    return tmp_path


def read(path: str) -> dict[str, object]:
    loaded = json.loads(Path(path).read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def test_eval_writes_summary_with_expected_schema() -> None:
    main(["eval", "--seeds", "20", "--runtimes", "naive,hardened"])
    summary = read("results/summary.json")
    assert sorted(summary) == ["failing_seeds", "generated_by", "runtimes", "seeds"]
    assert summary["generated_by"] == "make eval"
    assert summary["seeds"] == 20
    runtimes = summary["runtimes"]
    assert isinstance(runtimes, dict)
    expected = [
        "I1",
        "I2",
        "I3",
        "I4",
        "I5",
        "I6",
        "I7",
        "I8",
        "I9",
        "total_violating_seeds",
    ]
    for name in ("naive", "hardened"):
        assert sorted(runtimes[name]) == sorted(expected)
    assert runtimes["naive"]["I8"] is None
    assert runtimes["naive"]["I6"] is None
    assert isinstance(runtimes["hardened"]["I8"], int)
    assert isinstance(runtimes["hardened"]["I6"], int)
    failing = summary["failing_seeds"]
    assert isinstance(failing, dict)
    assert failing["naive"] == sorted(failing["naive"])
    assert runtimes["naive"]["total_violating_seeds"] == len(failing["naive"])


def test_summary_file_format() -> None:
    main(["eval", "--seeds", "3", "--runtimes", "hardened"])
    raw = Path("results/summary.json").read_bytes()
    assert raw.endswith(b"}\n")
    assert b"\r" not in raw
    assert raw.decode() == json.dumps(json.loads(raw), sort_keys=True, indent=2) + "\n"


def test_two_evals_give_byte_identical_files() -> None:
    args = ["eval", "--seeds", "15", "--runtimes", "naive,hardened"]
    main(args)
    first = [Path(f"results/{n}.json").read_bytes() for n in ("summary", "failures")]
    main(args)
    second = [Path(f"results/{n}.json").read_bytes() for n in ("summary", "failures")]
    assert first == second


def test_only_requested_runtimes_appear() -> None:
    main(["eval", "--seeds", "2", "--runtimes", "naive"])
    summary = read("results/summary.json")
    assert list(summary["runtimes"]) == ["naive"]  # type: ignore[call-overload]


def test_failures_are_sorted_and_have_the_documented_shape() -> None:
    main(["eval", "--seeds", "30", "--runtimes", "hardened,naive"])
    failures = json.loads(Path("results/failures.json").read_text(encoding="utf-8"))
    assert failures, "expected naive to fail at least one of 30 seeds"
    keys = [(f["runtime"], f["seed"]) for f in failures]
    assert keys == sorted(keys)
    for entry in failures:
        assert sorted(entry) == [
            "outcome",
            "restarts",
            "runtime",
            "seed",
            "violations",
        ]
        assert entry["violations"]
        for v in entry["violations"]:
            assert sorted(v) == ["detail", "invariant"]


def test_exit_code_is_zero_when_hardened_is_clean() -> None:
    assert main(["eval", "--seeds", "10", "--runtimes", "naive,hardened"]) == 0
    assert main(["eval", "--seeds", "10", "--runtimes", "naive"]) == 0


def test_exit_code_is_one_when_hardened_has_a_violation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def bad(seed: int, kind: RuntimeKind, config: object) -> RunResult:
        violations = [Violation(invariant="I1", detail="forced")] if seed == 2 else []
        return RunResult(
            seed=seed,
            runtime=kind,
            trace_hash="h",
            trace_bytes=b"",
            violations=violations,
            restarts=0,
            outcome="closed",
        )

    monkeypatch.setattr(cli, "evaluate_seed", bad)
    assert main(["eval", "--seeds", "3", "--runtimes", "hardened"]) == 1
    summary = read("results/summary.json")
    assert summary["failing_seeds"] == {"hardened": [2]}
    assert Path("results/failures.json").exists()


@pytest.mark.parametrize(
    "argv",
    [
        ["eval", "--seeds", "0", "--runtimes", "naive"],
        ["eval", "--seeds", "-1", "--runtimes", "naive"],
        ["eval", "--seeds", "x", "--runtimes", "naive"],
        ["eval", "--seeds", "2", "--runtimes", "bogus"],
        ["eval", "--seeds", "2", "--runtimes", "naive,naive"],
        ["eval", "--seeds", "2", "--runtimes", ""],
        ["eval", "--seeds", "2"],
        ["replay", "--seed", "1", "--runtime", "bogus"],
        [],
    ],
)
def test_bad_arguments_exit_with_2(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(argv)
    assert exc.value.code == 2
    assert not Path("results").exists()


def test_replay_writes_canonical_jsonl_and_prints_matching_hash(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["replay", "--seed", "1", "--runtime", "hardened"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith("run1 ")
    assert lines[1] == "run2 " + lines[0].removeprefix("run1 ")
    path = Path(lines[2])
    assert path == Path("results/replay/seed-1-hardened.jsonl")
    raw = path.read_bytes()
    assert b"\r" not in raw
    assert raw.endswith(b"\n") and not raw.endswith(b"\n\n")
    import hashlib

    assert hashlib.sha256(raw).hexdigest() == lines[0].removeprefix("run1 ")


def test_replay_with_differing_hashes_exits_1_and_writes_nothing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    hashes = iter(["aaa", "bbb"])

    def fake(seed: int, kind: RuntimeKind, config: object) -> RunResult:
        return RunResult(
            seed=seed,
            runtime=kind,
            trace_hash=next(hashes),
            trace_bytes=b"x\n",
            violations=[],
            restarts=0,
            outcome="closed",
        )

    monkeypatch.setattr(cli, "run_seed", fake)
    assert main(["replay", "--seed", "1", "--runtime", "naive"]) == 1
    out = capsys.readouterr().out
    assert "run1 aaa" in out and "run2 bbb" in out
    assert not Path("results").exists()


def test_new_invariant_keys_and_null_rules_for_all_runtimes() -> None:
    main(["eval", "--seeds", "12", "--runtimes", "naive,keyed,hardened"])
    runtimes = read("results/summary.json")["runtimes"]
    assert isinstance(runtimes, dict)
    for name in ("naive", "keyed", "hardened"):
        for key in ("I3", "I4", "I7"):
            assert isinstance(runtimes[name][key], int)
    for name in ("naive", "keyed"):
        assert runtimes[name]["I6"] is None
        assert runtimes[name]["I8"] is None
    assert isinstance(runtimes["hardened"]["I6"], int)
    assert list(runtimes["naive"]) == sorted(runtimes["naive"])


def test_two_three_runtime_evals_write_identical_files() -> None:
    main(["eval", "--seeds", "12", "--runtimes", "naive,keyed,hardened"])
    first = [Path(f"results/{n}.json").read_bytes() for n in ("summary", "failures")]
    main(["eval", "--seeds", "12", "--runtimes", "naive,keyed,hardened"])
    second = [Path(f"results/{n}.json").read_bytes() for n in ("summary", "failures")]
    assert first == second


@pytest.mark.parametrize("invariant", ["I3", "I4", "I6", "I7"])
def test_exit_code_is_one_for_a_hardened_violation_of_a_new_invariant(
    monkeypatch: pytest.MonkeyPatch, invariant: str
) -> None:
    def bad(seed: int, kind: RuntimeKind, config: object) -> RunResult:
        found = [Violation(invariant=invariant, detail="forced")]  # type: ignore[arg-type]
        return RunResult(
            seed=seed,
            runtime=kind,
            trace_hash="h",
            trace_bytes=b"",
            violations=found if kind == "hardened" else [],
            restarts=0,
            outcome="closed",
        )

    monkeypatch.setattr(cli, "evaluate_seed", bad)
    assert main(["eval", "--seeds", "2", "--runtimes", "naive,hardened"]) == 1
    assert read("results/summary.json")["runtimes"]["hardened"][invariant] == 2  # type: ignore[index]
    assert main(["eval", "--seeds", "2", "--runtimes", "naive"]) == 0


def test_report_missing_summary_exits_2() -> None:
    assert main(["report"]) == 2
    assert not Path("results/summary.md").exists()


def test_report_invalid_json_exits_2() -> None:
    Path("results").mkdir()
    Path("results/summary.json").write_text("{nope", encoding="utf-8")
    assert main(["report"]) == 2


def test_report_writes_stable_lf_file_without_touching_summary() -> None:
    main(["eval", "--seeds", "5", "--runtimes", "naive,keyed,hardened"])
    before = Path("results/summary.json").read_bytes()
    assert main(["report"]) == 0
    first = Path("results/summary.md").read_bytes()
    assert b"\r" not in first and first.endswith(b"\n")
    assert main(["report"]) == 0
    assert Path("results/summary.md").read_bytes() == first
    assert Path("results/summary.json").read_bytes() == before


def test_readme_missing_inputs_exit_2() -> None:
    assert main(["readme"]) == 2
    Path("README.md").write_text("x\n", encoding="utf-8")
    assert main(["readme"]) == 2
    assert not Path("results/summary.json").exists()
