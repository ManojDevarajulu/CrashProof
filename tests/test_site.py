import hashlib
import json
from pathlib import Path

import pytest

from crashproof.cli import main
from crashproof.sim.report import (
    FailuresFormatError,
    invariant_seeds,
    select_site_seeds,
)

_SEED1_HARDENED_HASH = (
    "32571347ac4fa35e59a9960eaeb97e3f05ce59bbf7184fe1f1eb4bdbe012595a"
)


def failure(runtime: str, seed: int, *invariants: str) -> dict[str, object]:
    return {
        "runtime": runtime,
        "seed": seed,
        "outcome": "closed",
        "restarts": 0,
        "violations": [{"invariant": i, "detail": "d"} for i in invariants],
    }


def test_invariant_seeds_dedupes_and_sorts() -> None:
    failures = [
        failure("naive", 9, "I1", "I1", "I2"),
        failure("naive", 3, "I1"),
        failure("keyed", 4, "I5"),
    ]
    assert invariant_seeds(failures) == {
        "keyed": {"I5": [4]},
        "naive": {"I1": [3, 9], "I2": [9]},
    }


@pytest.mark.parametrize(
    "entry",
    [
        "not an object",
        {"runtime": "naive", "seed": "1", "violations": []},
        {"runtime": "naive", "seed": 1, "violations": "I1"},
        {"runtime": "naive", "seed": 1, "violations": [{"detail": "no name"}]},
    ],
)
def test_invariant_seeds_rejects_malformed_entries(entry: object) -> None:
    with pytest.raises(FailuresFormatError):
        invariant_seeds([entry])


def test_select_site_seeds_starts_with_seed_1_and_covers_every_pair() -> None:
    by_invariant = {
        "naive": {"I1": [5, 8, 11], "I7": [5, 30]},
        "keyed": {"I5": [21, 37]},
    }
    seeds = select_site_seeds(by_invariant, 5)
    assert seeds == [1, 5, 21, 30, 37]
    assert select_site_seeds(by_invariant, 5) == seeds


def test_select_site_seeds_stops_when_queues_run_out() -> None:
    assert select_site_seeds({"naive": {"I1": [1, 2]}}, 20) == [1, 2]


def write_results(summary: object, failures: object) -> None:
    Path("results").mkdir()
    Path("results/summary.json").write_text(json.dumps(summary), encoding="utf-8")
    Path("results/failures.json").write_text(json.dumps(failures), encoding="utf-8")


def test_site_exports_index_and_verifiable_replays(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    summary = {"seeds": 3, "runtimes": {}, "failing_seeds": {}}
    write_results(summary, [failure("naive", 1, "I1", "I2")])
    assert main(["site"]) == 0
    data = Path("web/public/data")
    first = (data / "index.json").read_bytes()
    index = json.loads(first)
    assert index["summary"] == summary
    assert index["invariant_seeds"] == {"naive": {"I1": [1], "I2": [1]}}
    [seed] = index["seeds"]
    assert seed["seed"] == 1
    assert seed["scenario"]["carrier"] == "C"
    assert seed["runtimes"]["hardened"]["trace_hash"] == _SEED1_HARDENED_HASH
    naive = {v["invariant"] for v in seed["runtimes"]["naive"]["violations"]}
    assert naive == {"I1", "I2"}
    for kind, facts in seed["runtimes"].items():
        replay = (data / "replays" / f"seed-1-{kind}.jsonl").read_bytes()
        assert hashlib.sha256(replay).hexdigest() == facts["trace_hash"]
    assert main(["site"]) == 0
    assert (data / "index.json").read_bytes() == first


def test_site_removes_stale_replays(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    write_results({"seeds": 1}, [])
    stale = Path("web/public/data/replays/seed-99-naive.jsonl")
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b"old\n")
    assert main(["site"]) == 0
    assert not stale.exists()


def test_site_without_failures_file_exits_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    Path("results").mkdir()
    Path("results/summary.json").write_text("{}", encoding="utf-8")
    assert main(["site"]) == 2
    assert not Path("web").exists()


def test_site_with_malformed_failures_exits_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    write_results({}, [{"runtime": "naive"}])
    assert main(["site"]) == 2
