import argparse
import json
from collections.abc import Sequence
from pathlib import Path
from typing import cast, get_args

from crashproof.config import ScenarioConfig, load_scenario_config
from crashproof.models import Json
from crashproof.sim.report import build_report, splice_results
from crashproof.sim.runner import RunResult, RuntimeKind, evaluate_seed, run_seed

_INVARIANTS = ("I1", "I2", "I3", "I4", "I5", "I6", "I7", "I8", "I9")
_RESULTS = Path("results")


def _runtime_list(text: str) -> list[RuntimeKind]:
    valid = get_args(RuntimeKind.__value__)
    names = text.split(",")
    if len(set(names)) != len(names):
        raise argparse.ArgumentTypeError(f"duplicate runtime in {text!r}")
    for name in names:
        if name not in valid:
            raise argparse.ArgumentTypeError(f"unknown runtime {name!r}")
    return cast(list[RuntimeKind], names)


def _positive(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not an integer: {text!r}") from None
    if value < 1:
        raise argparse.ArgumentTypeError(f"must be >= 1: {value}")
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="crashproof")
    sub = parser.add_subparsers(dest="command", required=True)
    ev = sub.add_parser("eval", help="run seeds 1..S for each runtime")
    ev.add_argument("--seeds", type=_positive, required=True)
    ev.add_argument("--runtimes", type=_runtime_list, required=True)
    rp = sub.add_parser("replay", help="run one seed twice and write its trace")
    rp.add_argument("--seed", type=_positive, required=True)
    rp.add_argument("--runtime", choices=get_args(RuntimeKind.__value__), required=True)
    sub.add_parser("report", help="write results/summary.md from results/summary.json")
    sub.add_parser("readme", help="splice results/summary.md into README.md")
    return parser


def _write_json(path: Path, value: Json) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(value, sort_keys=True, indent=2) + "\n"
    path.write_bytes(text.encode("utf-8"))


def _counts(results: Sequence[RunResult], kind: RuntimeKind) -> dict[str, Json]:
    summary: dict[str, Json] = {}
    for invariant in _INVARIANTS:
        if invariant in ("I6", "I8") and kind != "hardened":
            summary[invariant] = None
            continue
        summary[invariant] = sum(
            any(v.invariant == invariant for v in r.violations) for r in results
        )
    summary["total_violating_seeds"] = sum(1 for r in results if r.violations)
    return summary


def _failure(result: RunResult) -> dict[str, Json]:
    return {
        "runtime": result.runtime,
        "seed": result.seed,
        "outcome": result.outcome,
        "restarts": result.restarts,
        "violations": [
            {"invariant": v.invariant, "detail": v.detail} for v in result.violations
        ],
    }


def _seeds(rows: Sequence[RunResult]) -> list[Json]:
    return [r.seed for r in sorted(rows, key=lambda r: r.seed)]


def _eval(seeds: int, runtimes: list[RuntimeKind], config: ScenarioConfig) -> int:
    by_kind: dict[RuntimeKind, list[RunResult]] = {
        kind: [evaluate_seed(seed, kind, config) for seed in range(1, seeds + 1)]
        for kind in runtimes
    }
    failing = {
        kind: [r for r in results if r.violations] for kind, results in by_kind.items()
    }
    summary: Json = {
        "generated_by": "make eval",
        "seeds": seeds,
        "runtimes": {kind: _counts(by_kind[kind], kind) for kind in runtimes},
        "failing_seeds": {kind: _seeds(rows) for kind, rows in failing.items()},
    }
    failures = sorted(
        (r for rows in failing.values() for r in rows),
        key=lambda r: (r.runtime, r.seed),
    )
    _write_json(_RESULTS / "summary.json", summary)
    _write_json(_RESULTS / "failures.json", [_failure(r) for r in failures])
    print(f"wrote {_RESULTS / 'summary.json'} and {_RESULTS / 'failures.json'}")
    return 1 if failing.get("hardened") else 0


def _replay(seed: int, kind: RuntimeKind, config: ScenarioConfig) -> int:
    first = run_seed(seed, kind, config)
    second = run_seed(seed, kind, config)
    if first.trace_hash != second.trace_hash:
        print(f"run1 {first.trace_hash}")
        print(f"run2 {second.trace_hash}")
        return 1
    path = _RESULTS / "replay" / f"seed-{seed}-{kind}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(first.trace_bytes)
    print(f"run1 {first.trace_hash}")
    print(f"run2 {second.trace_hash}")
    print(path)
    return 0


def _report() -> int:
    source = _RESULTS / "summary.json"
    try:
        summary = json.loads(source.read_text(encoding="utf-8"))
    except FileNotFoundError:
        print(f"error: {source} not found; run make eval first")
        return 2
    except json.JSONDecodeError as error:
        print(f"error: {source} is not valid JSON: {error}")
        return 2
    if not isinstance(summary, dict):
        print(f"error: {source} is not a JSON object")
        return 2
    path = _RESULTS / "summary.md"
    path.write_bytes(build_report(summary).encode("utf-8"))
    print(path)
    return 0


def _readme() -> int:
    readme = Path("README.md")
    report = _RESULTS / "summary.md"
    for path in (readme, report):
        if not path.is_file():
            print(f"error: {path} not found")
            return 2
    try:
        spliced = splice_results(
            readme.read_text(encoding="utf-8"), report.read_text(encoding="utf-8")
        )
    except ValueError as error:
        print(f"error: {readme}: {error}")
        return 2
    readme.write_bytes((spliced.rstrip("\n") + "\n").encode("utf-8"))
    print("README.md updated")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "report":
        return _report()
    if args.command == "readme":
        return _readme()
    config = load_scenario_config()
    if args.command == "eval":
        return _eval(args.seeds, args.runtimes, config)
    return _replay(args.seed, args.runtime, config)


if __name__ == "__main__":
    raise SystemExit(main())
