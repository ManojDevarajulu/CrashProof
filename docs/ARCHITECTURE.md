# ARCHITECTURE.md
Fixed design. Changes require human approval (AGENTS.md Rule 15).

## 1. Task plan (ids used in STATE.md "Next task" and branch names)

Cut 1
- T1 Scaffolding: pyproject.toml, Makefile (setup, test, lint only), folder layout, lint/type/test config
- T2 clock.py (virtual clock and discrete-event scheduler), rng.py, ids.py, tests/test_no_nondeterminism.py
- T3 models.py (Pydantic), sim/trace.py (TraceEvent, canonical JSONL, trace_hash), config loader, config/scenario_config.yaml
- T4 World: store.py, carrier A, refunds.py, events.py (no faults)
- T5 world/faults.py: F1 F2 F3 F5 and the crash hook
- T6 runtime/tools.py, agent/base.py, agent/scripted.py
- T7 runtime/naive.py
- T8 Hardened runtime: runtime/workflow.py, ledger.py, executor.py (UNKNOWN status, no reconcile yet)
- T9 runtime/reconcile.py
- T10 sim/invariants.py (I1 I2 I5 I8 I9), sim/scenario.py, sim/runner.py, cli.py (eval, replay), Makefile eval and replay targets, Gate 1

Cut 2
- C2-1 Carriers B and C
- C2-2 Faults F4 F6 F7 F8 (not built)
- C2-3 runtime/policy.py (P1-P6), config/policy_rules.yaml, simulated approver
- C2-4 Invariants I3 I4 I6 I7
- C2-5 runtime/keyed.py (keyed runtime)
- C2-6 agent/llm.py, cassettes, `make record` (not built)
- C2-7 1,000-seed eval, results/summary.json, `make report` (writes results/summary.md), Gate 2

Cut 3
- C3-1 viewer/index.html
- C3-2 README.md (embeds results/summary.md)
- C3-3 docs/DESIGN.md
- C3-4 Demo script (steps only; the human records it), Gate 3

## 2. Cuts and gates

Gate 1: on 200 seeds, hardened has 0 violations of I1 I2 I5 I8 I9; naive has at
least 1 violating seed; replaying a seed twice gives an identical trace_hash.
Fault probabilities in config/scenario_config.yaml are frozen before the first
eval. Changing them afterwards needs a DECISIONS.md entry.

Gate 2: on 1,000 seeds with ScriptedAgent, hardened has 0 violations of I1-I9.
`make eval` writes results/summary.json; `make report` writes results/summary.md.
LLMAgent runs on at most 50 seeds from cassettes and is reported in a separate table (not built).

Gate 3: a fresh clone runs `make setup && make eval && make replay SEED=1`
successfully, and the viewer loads the replay JSONL.

Never start a later cut before the human approves the earlier gate.

## 3. Repository layout (no new top-level folders)

    crashproof/
    ├── AGENTS.md  CLAUDE.md  GEMINI.md  STATE.md  LOG.md  README.md
    ├── Makefile  pyproject.toml  .env.example  .gitignore  .gitattributes
    ├── docs/   ARCHITECTURE.md SPEC.md INVARIANTS.md DECISIONS.md REVIEW.md DESIGN.md
    │           DEMO.md RUNBOOK.md design.html
    │           (docs/archive/ is created on demand by the human only)
    ├── src/crashproof/
    │   ├── clock.py  rng.py  ids.py  models.py  cli.py
    │   ├── config/   scenario_config.yaml  policy_rules.yaml
    │   ├── world/    store.py carriers.py refunds.py events.py faults.py
    │   ├── runtime/  workflow.py ledger.py executor.py reconcile.py policy.py
    │   │             tools.py naive.py keyed.py
    │   ├── agent/    base.py scripted.py
    │   └── sim/      scenario.py runner.py invariants.py trace.py report.py
    ├── viewer/index.html          (Cut 3 only)
    ├── tests/
    └── results/                   (generated only: summary.json summary.md failures.json replay/)

.venv/, caches and *.db are gitignored and not part of the layout. The ledger
uses an in-memory sqlite database per seed. Do not create files outside this layout
without approval.

## 4. Core design

### Scheduler
Single-threaded discrete-event scheduler in clock.py: a priority queue ordered by
(virtual_time, insertion_sequence). Ties are broken by insertion sequence only.
No asyncio, threads or real time.

### Workflow states
RECEIVED, ELIGIBILITY_CHECKED, LABEL_PENDING, LABEL_CREATED, AWAITING_RETURN_SCAN,
REFUND_PENDING, REFUND_CONFIRMED, CLOSED.
Terminal side states: DENIED, FAILED_NEEDS_REVIEW. ESCALATED (awaiting the approver) is not terminal.
Only transitions in the transition table in workflow.py are legal; any other raises.

### Agent tools (the agent proposes, the runtime executes)
get_order, check_policy, create_return_label, issue_refund, escalate_to_human, close_case.
No other tools. check_policy is advisory in Cut 1 (it returns eligibility facts
from the order); in Cut 2 the policy engine enforces the rules regardless.

### Effects ledger (hardened runtime)
Table `effects`: id, workflow_id, effect_type ("create_label" | "issue_refund"),
idempotency_key (UNIQUE), status, request, response, policy_decision_id (Cut 2),
attempts, created_vt, updated_vt.
Status set: INTENDED, EXECUTING, CONFIRMED, FAILED, UNKNOWN.
Idempotency key = sha256 hex of "workflow_id|effect_type|logical_step".
logical_step is "label" or "refund".
Flow per side effect: write INTENDED, (Cut 2: policy check), mark EXECUTING, call
the service with the key where supported, then on success mark CONFIRMED.
On timeout or crash the effect is UNKNOWN. After a simulated restart, any
EXECUTING effect is treated as UNKNOWN.
FAILED means "confirmed not applied downstream".

### Reconciliation (reconcile.py)
Never retry an UNKNOWN effect blindly. For each UNKNOWN effect, query the downstream
service by idempotency key (where supported) or by reference (reference = workflow_id).
If found applied: mark CONFIRMED and store the downstream response.
If found not applied: mark FAILED, then the executor may retry the same effect with
the same key. Maximum attempts per effect: max_attempts in scenario_config.yaml
(default 5); beyond that the workflow moves to FAILED_NEEDS_REVIEW.

### Crash model
The executor exposes crash_point(name). When the F5 fault fires, it raises
SimulatedCrash after the effect is applied downstream and before the ledger
confirms it. The runner catches SimulatedCrash and models a restart: all runtime
objects are discarded and rebuilt from the ledger database. In-memory ledger data
survives; nothing else does. The naive runtime has no ledger, so a restart loses
its memory of the effect.

### Policy rules (Cut 2; declarative in config/policy_rules.yaml; evaluated before every side effect)
- P1 refund amount <= amount paid for the returned lines.
- P2 refund amount > refund_cap requires a recorded human approval.
- P3 refund only after a return.scanned event exists for the workflow.
- P4 requested_at minus delivered_at <= 30 virtual days.
- P5 at most one return label per workflow.
- P6 deny if the order has fraud_risk = true.
Every allow or deny writes a policy_decisions row with the rule id and a reason.

### Fault types (seeded; probabilities in config/scenario_config.yaml)
- F1 timeout before the effect is applied (call raises Timeout; nothing applied)
- F2 timeout after the effect is applied (effect applied; call raises Timeout)
- F3 duplicate webhook delivery (an event is delivered twice)
- F4 out-of-order webhook delivery (two events delivered in swapped order) [Cut 2] (specified, not built)
- F5 runtime crash after apply and before ledger confirm (see Crash model)
- F6 rate limit: 429 with retry_after seconds, nothing applied [Cut 2] (specified, not built)
- F7 provider-specific error schema: errors use the provider's own format [Cut 2] (specified, not built)
- F8 long-delayed webhook: delivery delayed by up to max_webhook_delay [Cut 2] (specified, not built)
Cut 1 implements F1 F2 F3 F5 only.

### Runtimes (same agent, world and seeds; only the runtime layer differs)
- naive: executes each agent proposal once, straight against the world. Retries come only from the agent re-proposing a call.
  No keys, no ledger, no policy, no scan timeout.
- keyed (Cut 2): as naive, but sends the deterministic idempotency key to every
  provider that supports keys (carriers A and C and the refund service). No ledger,
  no reconciliation, no policy.
- hardened: keys, ledger, executor, reconciliation, policy engine, audit log.
All three runtimes (naive, keyed, hardened) are compared.

### Determinism contract
Same (seed, config, agent, runtime) gives a byte-identical trace and trace_hash.
LLMAgent determinism comes only from cassettes keyed by (seed, step, prompt_hash).
Any dict or set iteration that affects output must be explicitly sorted.
One workflow (one order, one return) per seed.

## 5. Standards
Code: small functions, explicit types, no global mutable state, clock/rng/ids
injected as parameters, no dead or commented-out code, no TODOs without a
DECISIONS.md entry, no speculative abstractions, specific exceptions (never return
None for failure). Prefer deleting code to adding code.
Trace fields: seq, vt, workflow_id, actor, event, data.
Tests: unit tests per module; a scenario test per fault type; a negative test per
invariant; tests/test_no_nondeterminism.py scans src/ for forbidden calls; a
regression test pins trace_hash for 5 fixed seeds; never time.sleep; never skip.
Docs: README opens with the generated results table (from results/summary.md),
then a 5-line explanation, a one-command quickstart, and a Limitations section
(simulation only; simulated APIs encode the author's assumptions; the scripted
agent is not a measure of any specific LLM; no claim of real-world accuracy).
docs/DESIGN.md covers intent, design, trade-offs and what was deliberately not built.
Git: small commits, imperative messages. Commits go directly to main, one per task.
No secrets, no hand-edited files under results/.

## 6. Commands
Each target is created by the task that builds its code, never stubbed earlier.
- make setup: create .venv if missing and `pip install -e ".[dev]"`  (T1)
- make test: `pytest -q`  (T1)
- make lint: `ruff check .` and `ruff format --check .` and `mypy --strict src`  (T1)
- make eval SEEDS=200 RUNTIMES=naive,hardened: writes results/summary.json and
  results/failures.json; exits non-zero only if hardened violates an applicable invariant  (T10)
- make replay SEED=n RUNTIME=hardened: runs the seed twice, asserts identical
  trace_hash, writes results/replay/seed-n-RUNTIME.jsonl, prints the hash  (T10)
- make report: reads results/summary.json, writes results/summary.md  (C2-7)
- make readme: runs make report, then splices results/summary.md into README.md  (C3-2)
