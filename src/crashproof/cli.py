import argparse
import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast, get_args

from crashproof.agent.llm import (
    LLM_MODEL_ID,
    MAX_RECORD_SEEDS,
    CallBudget,
    CallCapReached,
    Cassette,
    GeminiClient,
    LLMAgent,
    ModelCallError,
    ModelClient,
    cassette_paths,
    summarize_run,
)
from crashproof.config import ScenarioConfig, load_scenario_config
from crashproof.models import Json
from crashproof.sim.report import (
    FailuresFormatError,
    build_readme_report,
    build_report,
    invariant_seeds,
    select_site_seeds,
    splice_results,
)
from crashproof.sim.runner import (
    AgentFactory,
    RunResult,
    RuntimeKind,
    evaluate_seed,
    run_seed,
)
from crashproof.sim.scenario import Scenario

_INVARIANTS = ("I1", "I2", "I3", "I4", "I5", "I6", "I7", "I8", "I9")
_RESULTS = Path("results")
_SITE_DATA = Path("web/public/data")
_SITE_SEED_LIMIT = 20
_SITE_RUNTIMES: tuple[RuntimeKind, ...] = ("naive", "keyed", "hardened")
_CASSETTES = Path("tests/cassettes")
_RECORD_SUMMARY = "_record_summary.json"
_ENDED = (
    "ended_by_malformed_output",
    "ended_by_step_cap",
    "ended_by_call_budget",
)


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


def _record_seeds(text: str) -> int:
    value = _positive(text)
    if value > MAX_RECORD_SEEDS:
        raise argparse.ArgumentTypeError(
            f"at most {MAX_RECORD_SEEDS} seeds may be recorded: {value}"
        )
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
    rec = sub.add_parser("record", help="record LLM cassettes (human only, live)")
    rec.add_argument("--seeds", type=_record_seeds, required=True)
    sub.add_parser("report", help="write results/summary.md from results/summary.json")
    sub.add_parser("readme", help="splice results/summary.md into README.md")
    sub.add_parser("site", help="export summary and seed replays to web/public/data")
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


def _llm_factory(cassette: Cassette, client: ModelClient | None) -> AgentFactory:
    return lambda scenario: LLMAgent(scenario.request, cassette, client)


def _skipped_seeds(directory: Path, recorded: set[int]) -> list[int]:
    path = directory / _RECORD_SUMMARY
    if not path.is_file():
        return []
    skipped = json.loads(path.read_text(encoding="utf-8"))["skipped"]
    return sorted({int(row["seed"]) for row in skipped} - recorded)


def _llm_stats(
    cassettes: list[Cassette], kind: RuntimeKind, skipped: int
) -> dict[str, Json]:
    runs = [c.runs[kind] for c in cassettes if kind in c.runs]
    steps = [int(run["steps"]) for run in runs]
    stats: dict[str, Json] = {"seeds_recorded": len(runs), "seeds_skipped": skipped}
    for name in _ENDED:
        stats[name] = sum(1 for run in runs if run["ended"] == name)
    stats["mean_agent_steps"] = f"{sum(steps) / max(len(steps), 1):.2f}"
    return stats


def _collecting_factory(
    cassette: Cassette, client: ModelClient, agents: list[LLMAgent]
) -> AgentFactory:
    def make(scenario: Scenario) -> LLMAgent:
        agent = LLMAgent(scenario.request, cassette, client)
        agents.append(agent)
        return agent

    return make


def _eval_llm(
    runtimes: list[RuntimeKind], config: ScenarioConfig, directory: Path
) -> Json:
    """Replay recorded cassettes on every runtime; None when there are none."""
    cassettes = [Cassette.load(path) for path in cassette_paths(directory)]
    if not cassettes:
        return None
    by_kind: dict[RuntimeKind, list[RunResult]] = {
        kind: [
            evaluate_seed(c.seed, kind, config, agent_factory=_llm_factory(c, None))
            for c in cassettes
        ]
        for kind in runtimes
    }
    skipped = _skipped_seeds(directory, {c.seed for c in cassettes})
    return {
        "model": ", ".join(sorted({c.model for c in cassettes})),
        "seeds": [c.seed for c in sorted(cassettes, key=lambda c: c.seed)],
        "skipped_seeds": cast(list[Json], skipped),
        "runtimes": {
            kind: {
                **_counts(by_kind[kind], kind),
                **_llm_stats(cassettes, kind, len(skipped)),
            }
            for kind in runtimes
        },
        "failing_seeds": {
            kind: _seeds([r for r in rows if r.violations])
            for kind, rows in by_kind.items()
        },
    }


def _eval(
    seeds: int,
    runtimes: list[RuntimeKind],
    config: ScenarioConfig,
    cassettes: Path = _CASSETTES,
) -> int:
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
    llm = _eval_llm(runtimes, config, cassettes)
    if llm is not None and isinstance(summary, dict):
        summary["llm"] = llm
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


@dataclass
class RecordOutcome:
    attempted: list[int] = field(default_factory=list)
    saved: list[int] = field(default_factory=list)
    skipped: list[tuple[int, str]] = field(default_factory=list)


def _write_record_summary(
    directory: Path, outcome: RecordOutcome, budget: CallBudget
) -> Path:
    """Only the model id, seed lists, fixed reason codes and counts. Nothing from
    the environment, the SDK or the network goes into this file."""
    body: Json = {
        "model": LLM_MODEL_ID,
        "seeds_attempted": cast(list[Json], outcome.attempted),
        "seeds_saved": cast(list[Json], outcome.saved),
        "skipped": [{"seed": s, "reason": r} for s, r in outcome.skipped],
        "model_calls": budget.calls,
        "prompt_tokens": budget.prompt_tokens,
        "output_tokens": budget.output_tokens,
    }
    path = directory / _RECORD_SUMMARY
    _write_json(path, body)
    return path


def _record(
    seeds: int,
    config: ScenarioConfig,
    directory: Path,
    client: ModelClient,
    budget: CallBudget,
    outcome: RecordOutcome | None = None,
) -> int:
    """Record all three runtimes per seed; a seed is saved only when it completes."""
    kinds: list[RuntimeKind] = ["naive", "keyed", "hardened"]
    status = 0
    outcome = RecordOutcome() if outcome is None else outcome
    for seed in range(1, seeds + 1):
        path = directory / f"seed-{seed:04d}.json"
        cassette = Cassette.load(path) if path.is_file() else Cassette(seed)
        outcome.attempted.append(seed)
        try:
            for kind in kinds:
                agents: list[LLMAgent] = []
                run_seed(
                    seed,
                    kind,
                    config,
                    agent_factory=_collecting_factory(cassette, client, agents),
                )
                cassette.runs[kind] = summarize_run(agents)
        except CallCapReached as error:
            print(f"stopped at seed {seed}, not saved: {error}")
            outcome.skipped.append((seed, "call_cap_reached"))
            status = 1
            break
        except ModelCallError as error:
            print(f"stopped at seed {seed}, not saved: {error}")
            outcome.skipped.append((seed, "model_call_failed"))
            status = 1
            break
        outcome.saved.append(seed)
        print(cassette.save(directory))
    print(
        f"model {LLM_MODEL_ID}: calls {budget.calls}, "
        f"prompt tokens {budget.prompt_tokens}, output tokens {budget.output_tokens}"
    )
    return status


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
    readme_path = _RESULTS / "summary_readme.md"
    readme_path.write_bytes(build_readme_report(summary).encode("utf-8"))
    print(path)
    return 0


def _readme() -> int:
    readme = Path("README.md")
    report = _RESULTS / "summary_readme.md"
    if not report.is_file():
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


class SiteInputError(Exception):
    """A results file that make site needs is missing or malformed."""


def _read_results_json(name: str) -> Json:
    path = _RESULTS / name
    try:
        value: Json = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SiteInputError(f"{path} not found; run make eval first") from None
    except json.JSONDecodeError as error:
        raise SiteInputError(f"{path} is not valid JSON: {error}") from None
    return value


def _scenario_facts(trace_bytes: bytes) -> dict[str, Json]:
    """Merge the data of the scenario.* events at the head of a trace."""
    facts: dict[str, Json] = {}
    for line in trace_bytes.decode("utf-8").splitlines():
        event = json.loads(line)
        if not str(event["event"]).startswith("scenario."):
            break
        facts.update(event["data"])
    # The runner records scenario.carrier only for carriers B and C.
    facts.setdefault("carrier", "A")
    return facts


def _site_seed(seed: int, config: ScenarioConfig, replays: Path) -> Json:
    runtimes: dict[str, Json] = {}
    facts: dict[str, Json] = {}
    for kind in _SITE_RUNTIMES:
        result = run_seed(seed, kind, config)
        (replays / f"seed-{seed}-{kind}.jsonl").write_bytes(result.trace_bytes)
        facts = _scenario_facts(result.trace_bytes)
        runtimes[kind] = {
            "outcome": result.outcome,
            "restarts": result.restarts,
            "trace_hash": result.trace_hash,
            "violations": [
                {"invariant": v.invariant, "detail": v.detail}
                for v in result.violations
            ],
        }
    return {"seed": seed, "scenario": facts, "runtimes": runtimes}


def _site(config: ScenarioConfig) -> int:
    try:
        summary = _read_results_json("summary.json")
        failures = _read_results_json("failures.json")
        if not isinstance(summary, dict) or not isinstance(failures, list):
            raise SiteInputError("summary.json must be an object, failures.json a list")
        by_invariant = invariant_seeds(failures)
    except (SiteInputError, FailuresFormatError) as error:
        print(f"error: {error}")
        return 2
    replays = _SITE_DATA / "replays"
    replays.mkdir(parents=True, exist_ok=True)
    for stale in sorted(replays.glob("*.jsonl")):
        stale.unlink()
    seeds = select_site_seeds(by_invariant, _SITE_SEED_LIMIT)
    index: Json = {
        "generated_by": "make site",
        "summary": summary,
        "invariant_seeds": cast(Json, by_invariant),
        "seeds": [_site_seed(seed, config, replays) for seed in seeds],
    }
    _write_json(_SITE_DATA / "index.json", index)
    print(f"wrote {_SITE_DATA / 'index.json'} and {len(seeds)} seeds x 3 replays")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "report":
        return _report()
    if args.command == "readme":
        return _readme()
    config = load_scenario_config()
    if args.command == "record":
        budget = CallBudget()
        try:
            client = GeminiClient(budget)
        except ModelCallError as error:
            print(f"error: {error}")
            return 2
        outcome = RecordOutcome()
        status = _record(args.seeds, config, _CASSETTES, client, budget, outcome)
        _write_record_summary(_CASSETTES, outcome, budget)
        return status
    if args.command == "eval":
        return _eval(args.seeds, args.runtimes, config)
    if args.command == "site":
        return _site(config)
    return _replay(args.seed, args.runtime, config)


if __name__ == "__main__":
    raise SystemExit(main())
