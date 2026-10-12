# RUNBOOK.md

Human-authored and protected. Agents read ONLY the section for the current task id
(search for "## T8" etc.), plus Section 0 if told to. Specs here are fixed; extending
them needs human approval.

## 0. Standing decisions (apply to every task)

- Commits go directly to main, one per task. The human tags {id}-pass after reviews.
- The harness (runner, T10) owns Clock, Scheduler, Rng, IdGen, the world (store,
  carrier, refunds, bus), the FaultInjector and the ledger connection. They survive
  SimulatedCrash. Runtime and agent objects are rebuilt after a crash. After a crash
  the runner calls bus.clear_subscribers(), re-subscribes the customer first, then
  the runtimes.
- Retries are agent behavior in every runtime. A runtime executes each proposal once.
  Agents never see or set idempotency keys or the reference.
- reference == workflow_id, minted once per seed by the scenario generator through
  IdGen prefix "wf" before anything else, so it is identical across runtimes.
- Hardened idempotency key = sha256 hex of "{workflow_id}|{effect_type}|{logical_step}"
  with effect_type "create_label" or "issue_refund" and logical_step "label" or "refund".
- Fairness: fault verdicts depend only on (seed, fault, reference, kind, occurrence),
  so naive and hardened face identical faults for identical logical attempts. Never
  advantage hardened by handicapping naive.
- SimulatedCrash is a BaseException. src/ never contains a broad `except Exception`,
  `except BaseException` or bare `except` around provider or runtime calls.
- Trace values are int, str, bool or None only. No floats anywhere in trace data.
- ToolResult data shapes for effects: label {"label_id", "status"}; refund
  {"refund_id", "amount"}. Match naive.py.
- Results come only from `make eval`. Never hand-write or hand-edit anything in results/.
- Summary counts are the number of SEEDS with at least one violation of that invariant.
- Config values are frozen before the first eval. After that, changing one needs a
  DECISIONS.md entry and a disclosure in the README.
- One agent works on the repo at a time. Only the human edits protected files.

## 1. Prompt templates (fill; paste the text between the ~~~ lines)

PLAN (Opus 5.5, Claude Code, fresh session):

```text
Follow AGENTS.md Section 8 session start. Read ONLY the {ID} section of
docs/RUNBOOK.md plus docs/ARCHITECTURE.md, SPEC.md and INVARIANTS.md as needed.
Plan task {ID}. Do not write code, edit files or install software. The Spec in the
runbook is fixed; do not redesign it. Produce a plan of at most 60 lines: file list,
public API signatures, exact behavior on each edge case, the test list, and
questions for anything the Spec does not settle, each with your recommendation.
Stop.
```

IMPLEMENT (Sonnet 5.5 in Claude Code, or Gemini 3.1 Pro in Antigravity; fresh session):

```text
Follow AGENTS.md Section 8 session start, then do task {ID} following AGENTS.md
Section 6. Read ONLY the {ID} section of docs/RUNBOOK.md and Section 0. Do not
install software; if a tool is missing, stop and tell me. Commit directly to main.
Scope: exactly the files named in the Scope line of the {ID} section, plus STATE.md
(25 lines or fewer) and LOG.md. Do not edit any existing test, any file from an
earlier task (unless the Scope line lists it), models.py, pyproject.toml, or any
protected file. All earlier tests must pass unchanged. Write tests first. Run make
test and make lint and show the raw output. Update STATE.md, append the LOG.md
entry (put non-obvious choices on the Decision line), commit, report, and stop.
If the Spec and the existing code conflict, stop and report the exact conflict.
APPROVED PLAN AND ANSWERS (if any):
{paste here}
```

REVIEW A (Gemini 3.1 Pro in Antigravity, fresh session) and REVIEW B (Opus 5.5 in
Claude Code, critical tasks only; same text):

```text
Read AGENTS.md and docs/REVIEW.md. Review only the latest commit on main (git show
HEAD). If HEAD is not a {ID} commit, stop and say so. Do not edit files or install
software. Run make test and make lint and show raw output. Check: scope against the
{ID} Scope line; git diff --stat shows no earlier test file edited (unless the
Spec allows it); determinism (Rule 4); no floats in trace data; protected files,
models.py and pyproject.toml unchanged; STATE.md 25 lines or fewer. Then check every
bullet under "Review focus" in the {ID} section of docs/RUNBOOK.md. Report findings
and a verdict (PASS or blocking issues).
```

FIX (same implementer session, after a reviewer reports blocking issues):

```text
Fix only these findings, nothing else. Do not refactor or add features. Re-run
make test and make lint, show raw output, commit, report, and stop.
{paste the blocking findings}
```

## 2. Answering the planner's questions (Decision Rules, in order)

1. Spec text in this runbook, SPEC.md and ARCHITECTURE.md wins over everything.
2. If silent: pick the smallest scope. No new file, dependency, config key or
   abstraction unless the Spec names it.
3. Fairness: both runtimes see the same scenario, seed and fault verdicts.
4. Determinism: when in doubt, pick the option giving byte-identical reruns.
5. Fail loudly: raise a specific error rather than guess or return None for failure.
6. Anything that changes an invariant, a fault definition, a gate or a frozen config
   value: do not decide it. Record it under "Scope change requests" in DECISIONS.md,
   stop, and bring it to the person guiding the project.
   Paste the plan plus your answers into the IMPLEMENT template.

## 3. Failure playbook

- Tests or lint fail after the agent's attempt: let it fix. After two failed attempts,
  start a fresh Opus implement session with the same prompt plus the failing output.
- Reviewer reports blocking issues: use FIX. Non-blocking notes: ignore unless they
  touch determinism, fairness or scope.
- Gemini says PASS on a critical task but something feels off: run REVIEW B.
- An agent installed software, edited a protected file or went out of scope: revert
  that change (git checkout -- file, or git revert) and re-run with a stricter prompt.
- A gate fails: do NOT tweak config or invariants. Replay a failing seed and give the
  trace to Opus (see Gate procedures).
- Quota out: Gemini pool for bulk work, Claude pools for Sonnet/Opus. If everything is
  out, wait for the 5-hour window. STATE.md and LOG.md lose nothing.

## 4. Task table

Task | Plan | Implement | Review | About
T8 | Opus | Sonnet | A + B | 60 min
T9 | none | Sonnet | A + B | 35 min
T10 | Opus | Sonnet | A + B | 90 min
C2-1 | none | Gemini 3.1 Pro | A | 30 min
C2-2 | Opus | Sonnet | A | 40 min
C2-3 | Opus | Sonnet | A + B | 70 min
C2-4 | none | Sonnet | A + B | 40 min
C2-5 | none | Gemini 3.1 Pro | A | 20 min
C2-6 | Opus | Gemini 3.1 Pro | A | 45 min
C2-7 | none | Sonnet | A + B | 40 min
C3-1 | none | Gemini 3.8 Flash | human | 35 min
C3-2 | none | Sonnet | human | 20 min
C3-3 | Opus drafts | you rewrite | Opus | 25 min
C3-4 | none | Sonnet | human | 15 min

## T8 Hardened runtime: ledger, executor, state machine

Scope: runtime/ledger.py, runtime/executor.py, runtime/workflow.py (state machine AND
the HardenedRuntime class), tests/test_ledger.py, test_executor.py, test_workflow.py.
Cut 1 has no reconciliation or policy yet.

Ledger (ledger.py), in-memory sqlite3, connection owned by the harness:

- Table effects: id INTEGER PRIMARY KEY AUTOINCREMENT, workflow_id, effect_type,
  idempotency_key UNIQUE, status, request (JSON text), response (JSON text or NULL),
  policy_decision_id (NULL until Cut 2), attempts INTEGER, created_vt, updated_vt.
- Table workflows: workflow_id PRIMARY KEY, state, outcome (NULL), scan_received
  INTEGER 0/1, updated_vt.
- JSON columns use json.dumps(sort_keys=True, separators=(",", ":")).
- EffectRow: frozen pydantic model mirroring effects (request and response parsed).
- Status transitions (anything else raises LedgerError): INTENDED to EXECUTING;
  EXECUTING to CONFIRMED, FAILED or UNKNOWN; UNKNOWN to CONFIRMED or FAILED;
  FAILED to EXECUTING (retry). CONFIRMED is final. attempts increments on every
  move to EXECUTING.
- Methods: insert_intended, get_effect(key), mark_executing, mark_confirmed(key,
  response), mark_failed(key, response), mark_unknown, recover() (every EXECUTING
  row becomes UNKNOWN; returns the list of keys), effects_for(workflow_id) ordered by
  id, all_effects(), get_workflow, upsert_workflow, set_scan_received, set_outcome.
  updated_vt always comes from the Clock.

Executor (executor.py):

- idempotency_key(workflow_id, effect_type) -> str per Section 0.
- Executor(ledger, carrier, refunds, faults, trace, clock, max_attempts, reconciler=None)
  where reconciler: Callable[[EffectRow], Literal["confirmed","failed"]] | None. This
  one optional parameter is the only seam for T9. Add nothing else speculative.
- EffectResult(kind: Literal["confirmed","timed_out","failed","exhausted"], data, error).
- execute(workflow_id, effect_type, order_id, amount=None) -> EffectResult:
  1. key = idempotency_key(...). Look up the row.
  2. CONFIRMED row: if the stored request equals the new request, return the stored
     response as "confirmed" (trace executor.dedup); if it differs, return "failed"
     with error "request_mismatch" and call nothing.
  3. UNKNOWN row: if reconciler is None, return "timed_out" (trace
     executor.unknown_pending). Otherwise call reconciler(row): "confirmed" returns
     the stored response; "failed" falls through to a retry.
  4. FAILED row, or a row left INTENDED: retry if attempts < max_attempts, else return
     "exhausted".
  5. No row: insert INTENDED. (Cut 2 puts the policy check here.)
  6. Mark EXECUTING. Call the provider. Send idempotency_key only if
     carrier.supports_keys is true (always true in Cut 1). The refund service always
     gets the key.
  7. Provider raises faults.Timeout (catch it specifically): mark UNKNOWN, return
     "timed_out". IdempotencyConflict or InvalidRefundAmount: mark FAILED with
     {"error": ClassName}, return "failed" with that error.
  8. Success: if faults is not None, call faults.crash_point("label_applied" or
     "refund_applied", workflow_id) BEFORE confirming. Then mark CONFIRMED with the
     response and return "confirmed". A SimulatedCrash therefore leaves the row
     EXECUTING.
- Trace actor "runtime": executor.dedup, executor.unknown_pending, executor.confirmed,
  executor.unknown, executor.failed, executor.exhausted.

State machine and runtime (workflow.py):

- MAIN = RECEIVED, ELIGIBILITY_CHECKED, LABEL_PENDING, LABEL_CREATED,
  AWAITING_RETURN_SCAN, REFUND_PENDING, REFUND_CONFIRMED, CLOSED. Terminal:
  CLOSED, FAILED_NEEDS_REVIEW. DENIED and ESCALATED exist as names but are unused
  until Cut 2.
- advance_to(target): if target is at or before the current state in MAIN, it is a
  replay no-op (trace workflow.replay); if it is exactly the next state, advance
  (trace workflow.advance with from and to); anything else raises WorkflowError.
  FAILED_NEEDS_REVIEW is reachable from any non-terminal state. The state and outcome
  are persisted in the workflows table on every change.
- HardenedRuntime(request, agent, *, store, carrier, refunds, bus, trace, clock,
  ledger, max_attempts, faults=None, reconciler=None): same driving protocol as
  NaiveRuntime (read naive.py and agent/base.py; follow T6's Observation protocol).
  start(): call ledger.recover(); load or create the workflow row (RECEIVED); if
  the workflow is already terminal, do nothing; otherwise subscribe the scan handler
  and drive the agent. If the persisted scan_received is 1, the Observation after a
  Wait starts with scan_received True.
- The scan handler filters on type return.scanned and this workflow_id, persists
  scan_received=1, resumes the agent, and ignores everything once terminal.
- Mapping: check_policy executed to ELIGIBILITY_CHECKED. Before the label effect:
  LABEL_PENDING. Label confirmed: LABEL_CREATED. Agent returns Wait: AWAITING_RETURN_SCAN.
  Before the refund effect: REFUND_PENDING. Refund confirmed: REFUND_CONFIRMED.
  close_case: CLOSED, outcome "closed".
- Executor result mapping: confirmed to ToolResult(ok=True, data); timed_out to a
  timed_out Observation (last_result None); failed to ToolResult(ok=False, error);
  exhausted to terminal FAILED_NEEDS_REVIEW with outcome "needs_review". Agent Done
  without close_case: FAILED_NEEDS_REVIEW with outcome "gave_up". Invalid ToolCall
  args: outcome "invalid_call" (traced). Other tools (get_order, check_policy,
  escalate_to_human) behave as in naive.py.
- Trace names match naive where the meaning is the same (agent.proposed,
  runtime.tool_executed, runtime.tool_timeout, runtime.terminal).

Tests: ledger transition table (legal and illegal), recover(), JSON round trip,
effects ordering; happy path (every label and refund record carries the derived key,
ledger rows CONFIRMED, state CLOSED); dedup by a second runtime plus agent for the
same workflow (one label, one refund); request_mismatch; F2 on the label leaves the
label applied and the row UNKNOWN, the agent retries and gets timed_out until it
gives up, no duplicate label; forced F5: SimulatedCrash propagates, row EXECUTING;
a new runtime after bus.clear_subscribers() recovers it to UNKNOWN and makes no
provider call; attempts cap returns "exhausted"; state machine legal sequence, skip
forward raises, replay is a no-op, persistence across a rebuilt runtime; persisted
scan_received; carrier with supports_keys False (test-local fake) gets no key;
determinism (identical trace_hash twice); a source-text test that fails on any broad
except in the three modules.

Review focus:

- The crash point is between the provider call returning and mark_confirmed.
- UNKNOWN is never retried blindly (reconciler None returns timed_out).
- The key sent to providers equals the derived key, and is not sent when supports_keys
  is False.
- Timeout is caught specifically; no broad except.
- Hardened differs from naive only by keys, ledger, state machine (no extra or
  skipped agent-visible steps).
- The ledger enforces its transition table, and attempts count correctly.
- No policy code, no reconcile code, no new files, only the one optional reconciler seam.

## T9 Reconciliation

Scope: runtime/reconcile.py, tests/test_reconcile.py. Do not edit earlier source files.

- make_reconciler(ledger, carrier, refunds, trace, clock) returns a Reconciler (the
  callable type from T8). For an UNKNOWN row: label effect: carrier.get_label_by_key(key)
  if carrier.supports_keys, else the record found by get_label_by_reference(workflow_id).
  Refund effect: refunds.get_by_key(key). Found: ledger.mark_confirmed with a response in
  the standard shape, trace reconcile.applied, return "confirmed". Not found:
  ledger.mark_failed, trace reconcile.not_applied, return "failed". Reads are never faulted.
- reconcile_workflow(workflow_id, ledger, reconciler) -> int: calls ledger.recover()
  first, then reconciles every UNKNOWN effect of the workflow in id order; returns how
  many it resolved. The runner calls it at the end of every hardened run (T10).
- Integration happens by passing the reconciler into Executor and HardenedRuntime
  (constructor arguments from T8). T9 edits no existing file.
  Tests (with forced probabilities or deterministically searched seeds, asserting a seed
  exists): F2 on a refund: the agent retries, reconciliation finds it applied,
  CONFIRMED, exactly one refund, no second provider call; F1 on a refund: not found,
  FAILED, retried with the same key, exactly one refund; F5 after a refund: restart,
  recover, reconcile, one refund; the agent gives up with an UNKNOWN row left,
  reconcile_workflow resolves it; same for labels; a fake carrier with supports_keys
  False resolves by reference; a row for something the provider never saw becomes FAILED;
  reconcile_workflow returns the right count; determinism; no broad except.
  Review focus: no blind retry anywhere; the statuses written match the T8 transition
  table; reconciliation reads only (no side effects on the world); the by-reference path
  is used only when supports_keys is false.

## T10 Invariants, scenario generator, runner, CLI (Gate 1)

Scope: sim/invariants.py, sim/scenario.py, sim/runner.py, cli.py, Makefile (add eval
and replay targets in the file's existing style), config/__init__.py and
config/scenario_config.yaml (new sections below), tests/test_invariants.py,
test_scenario.py, test_runner.py, test_cli.py, and tests/test_config.py (ONLY to add the
new required sections to its YAML fixtures and to extend the freeze test; list every
changed line in the report; do not weaken any assertion).
Before running T10, the human adds a DECISIONS.md entry: "T10 adds the config sections
timing, run and behavior_weights, frozen before the first eval; restarts are capped per
seed by run.max_restarts."

Config additions (frozen with the F values):
timing: {duplicate_request_delay: 60, scan_delay_min: 3600, scan_delay_max: 7200}
run: {max_restarts: 5, max_events: 10000}
behavior_weights: {normal: 6, duplicate_request: 2, never_ships_back: 2}
Pydantic models are frozen, strict, extra="forbid"; scan_delay_max >= scan_delay_min;
at least one weight above 0.

Scenario (scenario.py): make_scenario(seed, config, ids) with rng = Rng(seed).fork("scenario"):
mint workflow_id first (IdGen "wf"), then order_id ("ord") and customer_id ("cus"). 1 to 3
lines, sku "sku-1".., qty 1 to 3, unit_price 500 to 5000, paid_amount computed,
delivered_at 0, carrier "A", fraud_risk False. Request: line_skus = all skus (full
return), reason "changed_mind", requested_at 0. Behavior drawn by cumulative weight over
the fixed order [normal, duplicate_request, never_ships_back]. scan_delay = randint
between scan_delay_min and scan_delay_max. Cut 1 uses only these three behaviors.

Runner (runner.py): run_seed(seed, runtime_kind, config) builds a fresh harness per
call: Clock, Scheduler, Rng(seed), IdGen(seed), TraceRecorder, Store, EventBus (with the
injector), CarrierA, RefundService, FaultInjector, and for hardened a Ledger and
reconciler. runtime_kind in {"naive", "hardened"}. The only difference between kinds is
which runtime class is built. Flow:

- Customer: harness-owned subscriber. On the first label.created for this workflow,
  unless the behavior is never_ships_back, publish return.scanned with delay scan_delay
  (trace world customer.ships). Duplicates of label.created are ignored.
- Start one runtime plus a new ScriptedAgent(request). For duplicate_request, schedule a
  second runtime plus agent at now + duplicate_request_delay.
- Drain with scheduler.run_until_idle(max_events). On SimulatedCrash: restarts += 1; if it
  exceeds run.max_restarts stop with outcome "crash_loop"; else bus.clear_subscribers(),
  re-subscribe the customer, rebuild every runtime already started with a new agent
  and start it, then continue draining.
- At the end of a hardened run: ledger.recover() and reconcile_workflow(...).
- RunResult(seed, runtime, trace_hash, trace_bytes, violations, restarts, outcome).
  violations come from the invariant checkers, which read only world records (refund
  service, carrier, ledger), never runtime self-reports.

Invariants (invariants.py), each returns a list of Violation(invariant, detail):
I1 refunds per reference <= 1 (all runtimes). I2 total refunded per order <= paid_amount.
I5 labels per reference <= 1. I8 (hardened only, null for naive): every refund has exactly
one CONFIRMED issue_refund ledger row with the same idempotency_key, and every CONFIRMED
issue_refund row has exactly one refund. I9: run each (seed, runtime) twice with fresh
harnesses; differing trace_hash is a violation. Each invariant has a negative test built
from a tiny hand-made state that violates it, plus a clean state that passes.

CLI (cli.py, argparse, run with python -m crashproof.cli):

- eval --seeds S --runtimes naive,hardened: seeds are 1..S. For each seed and runtime,
  run twice (I9). Write results/summary.json (sort_keys, indent 2, no timestamps,
  generated_by "make eval", seeds, runtimes {name: {I1, I2, I5, I8, I9 counts as seed
  counts or null when not applicable, total_violating_seeds}}, failing_seeds {name:
  sorted list}) and results/failures.json (sorted by runtime then seed, each with its
  violations). Exit code 1 if hardened has any violation of an applicable invariant.
- replay --seed n --runtime hardened: run the seed twice, assert identical trace_hash,
  write results/replay/seed-n-RUNTIME.jsonl with Path.write_bytes(trace_bytes), print
  the hash.
- Makefile: eval (SEEDS=200 and RUNTIMES=naive,hardened by default) and
  replay (SEED=1, RUNTIME=hardened by default).
  Tests also cover: scenario determinism and behavior weights (searching seeds 1..200,
  all three behaviors occur); runner determinism; restart cap; naive creates two labels
  after a forced F5 restart; hardened does not; duplicate_request makes two refunds in
  naive (deterministic fault-free run) and one in hardened; summary.json schema and
  determinism (two evals give identical bytes); replay output has no CR characters and
  ends with one newline.
  Review focus:
- Invariants use world records and the ledger only, never runtime self-reports.
- The two runtimes are built identically except the runtime class; same scenario, same
  seed, same fault verdicts.
- The scheduler, customer and restart logic are deterministic; the restart cap exists.
- No tuning of config values; config additions are exactly the ones above; the
  test_config.py diff is additive and weakens nothing.
- results/ files are only written by the CLI; no hand-written numbers anywhere.
- Negative tests for I1, I2, I5, I8, I9 exist and would fail if the checker were a no-op.

### Gate 1 procedure

1. Run make eval SEEDS=200, then read results/summary.json yourself.
2. Pass condition: hardened shows 0 for I1, I2, I5, I8, I9; naive shows at least 1
   violating seed; make replay on any seed prints an identical hash twice.
3. Pick one failing naive seed, run make replay SEED={n} RUNTIME=naive, and read the
   JSONL. Confirm the failure story is real (a lost response or crash caused a
   duplicate) and not a harness bug.
4. If naive shows 0 violations: check wiring first (is the injector attached, does
   the agent retry). Do not tune probabilities without a DECISIONS.md entry.
5. If hardened shows any violation: it is a real bug. Replay the failing seed and give
   the trace plus the code to an Opus session with: "Explain exactly why this run
   violates {invariant}. Do not fix anything." Then fix with the IMPLEMENT template.
6. Tag gate1-pass.

## C2-1 Carriers B and C (sketch; run the Opus plan if unsure)

Scope: world/carriers.py, runtime/naive.py, workflow.py, executor.py, reconcile.py
(annotation-only edits to use the Protocol), config additions, tests.

- Add a LabelProvider Protocol (create_label, get_label_by_reference, labels,
  supports_keys; get_label_by_key only when supports_keys). Change runtime type
  annotations from CarrierA to LabelProvider. No behavior change.
- CarrierB "GlobeLine": supports_keys False; the key argument is accepted and ignored; a
  repeated call creates a second label; create_label returns status "pending", records
  the label, and publishes label.created after carrier_b_label_delay (config);
  error schema {error: {type, detail}}.
- CarrierC "NordPost": synchronous, supports keys like A, error schema {status,
  reason_code}. Its return.scanned webhooks are delivered twice and later by
  carrier_c_scan_extra_delay (customer behavior in the runner, config).
- The runner builds exactly one carrier per seed from order.carrier, so runtimes stay
  unchanged. Scenario generation gains carrier weights in config.
- New config keys need a DECISIONS.md entry from the human first.
  Review focus: A's behavior and all earlier tests unchanged; annotation-only edits in
  runtimes; B truly lacks keys; no runtime edits beyond annotations.

## C2-2 Faults F4 F6 F7 F8 (sketch; Opus plan required)

Scope: world/faults.py, small edits to world modules, runtime/naive.py and workflow.py
(map RateLimited to ToolResult), agent/scripted.py (rate-limit retry), config additions,
tests. Every new fault uses its own base.fork("F4") etc., so F1..F5 verdicts never change
(add a regression test pinning the old decisions).

- F4: extra delivery delay f4_extra_delay on a webhook, so a later event can overtake it.
- F6: provider raises RateLimited(retry_after: int) before applying; runtimes return
  ToolResult(ok=False, error="rate_limited", data={"retry_after": n}); the agent
  re-proposes the identical call (counts toward max_retries). No virtual wait is modeled.
- F7: provider returns an error in its own schema (nothing applied); runtimes
  normalize to ToolResult(ok=False, error="provider_error"); naive agent gives up;
  hardened marks the effect FAILED and returns failed.
- F8: extra delivery delay drawn from 1..max_webhook_delay.
  Open questions for Opus: where F4 and F8 apply (all webhooks or return.scanned only),
  and how the agent distinguishes rate_limited from a hard failure.

## C2-3 Policy engine and simulated approver (sketch; Opus plan required)

Scope: runtime/policy.py (engine, ApprovalLog, SimulatedApprover), config/policy_rules.yaml,
runtime/ledger.py (policy_decisions table), runtime/executor.py and workflow.py (policy hook
at step 5 and ESCALATED/DENIED handling), agent/scripted.py (Cut 2 behaviors), tests.

- Rules P1..P6 per ARCHITECTURE. refund_cap (default 20000) lives in policy_rules.yaml.
- Every allow or deny writes a policy_decisions row (rule id, reason). Denied effects are
  marked FAILED and never reach the provider.
- P2 over the cap: the runtime enters ESCALATED and calls the SimulatedApprover, which
  approves or denies after a seeded virtual delay per config; the decision lands in the
  ApprovalLog; on approval the runtime re-evaluates and executes.
- Cut 2 ScriptedAgent behaviors: occasionally propose a refund before the scan; propose an
  over-cap amount for high_value_over_cap orders. After a policy denial the agent
  continues (waits for the scan) instead of giving up.
- Scenario behaviors added: outside_window, high_value_over_cap, fraud_flagged,
  partial_return; the oracle verdict (SPEC section 6) is attached to each scenario.
- Naive and keyed have no policy, so they should violate I3 and I4 on those scenarios.
- Add a hardened scan timeout (config, 172800 seconds): with no scan by then, the
  workflow becomes FAILED_NEEDS_REVIEW with outcome "scan_timeout" (needed for I7).
  Review focus: policy runs before every side effect; denied effects never reach the
  provider; the oracle is used only by invariants, never by runtimes.

## C2-4 Invariants I3 I4 I6 I7

Scope: sim/invariants.py, sim/runner.py, tests. I3: refund only after a return.scanned
event and only if the oracle says eligible. I4: refunds above refund_cap need a
recorded approval. I6: every CONFIRMED effect has a policy_decisions row (hardened only).
I7: after the queue drains, advance virtual time 72 hours, drain again; any workflow still
non-terminal is stuck. Negative tests for each. Update INVARIANTS-facing report fields.
Review focus: checks use world records, trace order and the oracle, never runtime
self-reports; the 72-hour step actually runs.

## C2-5 Keyed runtime

Scope: runtime/keyed.py, tests/test_keyed_runtime.py, runner wiring for runtime_kind
"keyed". It is naive plus the executor's idempotency_key() sent to every provider that
supports keys. No ledger, no reconcile, no policy, no state machine. Same agent, same
retry semantics.
Review focus: keyed differs from naive only by keys; it fails realistically on carrier B,
on policy invariants, and on liveness, so the comparison is honest.

## C2-6 LLMAgent and cassettes (sketch; Opus plan required)

Scope: agent/llm.py, tests/test_llm_agent.py, cassettes/, Makefile (record target),
pyproject.toml (the human approves adding google-genai under an optional extra named
llm, NOT in dev), runner support for agent="llm".

- Gemini via Vertex AI (google-genai). GEMINI_MODEL, GCP_PROJECT and GCP_LOCATION come from
  env vars; the human decides the model name first and records it in DECISIONS.md.
- Output must be one Pydantic-validated ToolCall; invalid output counts as a failed step and
  is recorded.
- Cassette key (seed, step, prompt_hash); a cassette miss raises an error and never calls the
  network. make test and make eval never touch the network. Only `make record SEEDS=n`
  (human only, max 50) does live calls.
- Lazy import of google.genai inside the record path only; the T2 scan exemption covers
  agent/llm.py.
- Reported as a separate table (at most 50 seeds), never mixed with the ScriptedAgent results.
  Review focus: no network in tests; replay is byte-deterministic; no keys in the repo.

## C2-7 1,000-seed eval, report and Gate 2

Scope: sim/report.py, cli.py (report), Makefile (report), tests.

- eval over naive, keyed, hardened. summary.json gains I3 I4 I6 I7 and the LLM table.
- make report writes results/summary.md with grouped columns (Duplicates I1 I5, Money I2,
  Policy I3 I4, Audit I6 I8, Liveness I7, Replay I9; "n/a" for non-applicable).
  Gate 2 procedure: run make eval SEEDS=1000 RUNTIMES=naive,keyed,hardened and make report.
  Pass condition: hardened shows 0 for I1..I9. Keyed should still fail somewhere
  (carrier B duplicates, policy, liveness). If keyed shows 0 everywhere, check that carrier
  B and the policy scenarios are really exercised before believing it. Tag gate2-pass.

## C3-1 Trace viewer

Scope: viewer/index.html only. One static file, plain JavaScript, no framework or CDN. It
loads a replay JSONL through a file picker (FileReader), shows an event timeline with
actors in separate lanes, highlights fault events, and shows each event's data when
clicked. It handles a malformed line by showing an error, not by crashing.
Review: you open it with a real replay file.

## C3-2 README

Scope: README.md, sim/report.py (splice the results table between the markers

<!-- RESULTS:START --> and <!-- RESULTS:END -->), tests.

README opens with the generated table (copied only from results/summary.md), then 5
lines of explanation, the one-command quickstart, and Limitations (simulation only; the
simulated APIs encode the author's assumptions; the scripted agent is not a measure of any
specific LLM; no claim of real-world accuracy).

## C3-3 DESIGN.md

Scope: docs/DESIGN.md. Opus drafts from the code and the docs; you rewrite it in your own
words. Cover intent, the design, trade-offs, and what was deliberately not built and why.

## C3-4 Demo script and Gate 3

Scope: docs/DEMO.md (steps only; you record it). Show one naive failing seed in the viewer,
the same seed under hardened, then the results table. Gate 3: in a fresh clone (WSL or
Linux), run make setup && make eval && make replay SEED=1, and open the viewer. Tag gate3-pass.

## 5. Release checklist

- Real numbers copied from results/summary.md only.
- Say plainly that it is a simulation and name its limits.
