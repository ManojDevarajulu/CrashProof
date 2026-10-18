# AGENTS.md: Crashproof

Read this file fully, then do the session start in Section 8, before writing any
code. These rules override your defaults and any helpful-seeming suggestion.

## 1. Purpose

Crashproof is a returns/refund agent runtime plus a deterministic simulation
harness. It tests, in simulation, whether a runtime around an agent prevents
double execution, policy violations and stuck workflows under injected faults,
and shows that any failure replays exactly from a seed. It is a portfolio project, not a
product. Smaller and correct beats larger and impressive.

Headline result: a table of invariant violations per runtime (naive, keyed,
hardened) over S seeds, from measured runs only.

## 2. Absolute rules

1. **Scope is closed.** Build only what Section 3 lists as IN SCOPE. Anything
   unlisted is out of scope, even if small or obviously good. If unsure, stop and ask.
2. **No fabricated numbers.** Results come only from `make eval` output in
   `results/`. Placeholders must be literally `TBD`.
3. **No new dependencies** beyond the allowlist in Section 4 without human approval.
4. **Determinism is sacred.** In `src/`, never use `datetime.now`, `time.time`,
   `time.sleep`, module-level `random`, `uuid4`, `os.urandom`, network I/O,
   threads or asyncio. Use `clock.py`, `rng.py`, `ids.py`. A test enforces this.
   Exemptions, exactly three: rng.py may use random.Random(seed) instances;
   agent/llm.py is allowed network I/O and runs only under `make record`;
   adapters/stripe_refunds.py is allowed network I/O and runs only under
   `make demo-real` with a test-mode key. The scan test allowlists these three.
5. **One task at a time.** Finish, test, report. No unrelated refactors.
6. **Tests before done.** `make test` and `make lint` must pass. Never skip or
   delete tests.
7. **Never weaken a test, invariant or policy rule to pass.** Changing one
   needs human approval and a `docs/DECISIONS.md` entry.
8. **Fair baseline.** Runtimes naive, keyed and hardened use the same agent, scenarios,
   seeds and world. Only the runtime layer differs.
9. **No hidden failures.** No `except: pass`, no silent swallowing.
10. **Ask, don't guess** when a requirement is ambiguous or conflicting.
11. **LLM calls are recorded, never live in tests or bulk runs.** Live Gemini
    calls only via `make record` run by the human (max 50 seeds). `make test`
    and `make eval` need zero network and zero credentials.
12. **Temperature 0 is not determinism.** Determinism comes only from cassettes
    keyed by `(seed, step, prompt_hash)`.
13. **Human review gate.** After each task, stop. The human reviews the diff
    before the next task.
14. **Single active agent.** Only one coding agent works on the repo at a time.
    No parallel agents. Work on feature branches; merge each task through a pull request with a structured description (Intent, Trade-offs, Verification). The human tags each reviewed task.
15. **Protected files.** Only the human edits `AGENTS.md`, `CLAUDE.md`,
    `GEMINI.md`, `docs/SPEC.md`, `docs/INVARIANTS.md`, `docs/ARCHITECTURE.md`,
    `docs/REVIEW.md`, `docs/RUNBOOK.md`. Propose changes in `docs/DECISIONS.md` instead.
16. **Log every task.** Before reporting done, you must:
    a. Overwrite `STATE.md` (max 25 lines, current truth only).
    b. Append one entry to `LOG.md` (max 8 lines, format in its header).
    Both must be based only on commands you ran, never on intent. Never edit or
    delete earlier LOG.md entries. Outside the task's own files, STATE.md and
    LOG.md are the only files you may edit, except that you may append to
    docs/DECISIONS.md under the headings 'Scope change requests' and 'Pending
    approval' only, never editing existing entries. Agent names in LOG.md are
    claude, antigravity or human.
17. **Logs never replace evidence.** If STATE.md or LOG.md disagrees with the
    code or test output, trust the code and tests, then report the mismatch.
18. **Read the logs cheaply.** Read STATE.md in full. Read only the last 5
    entries of LOG.md (`tail -n 60 LOG.md`). Never read the whole LOG.md and never
    re-scan the whole repo to "get oriented". Use `git log --oneline -15` for history.

## 3. Scope

IN SCOPE: one workflow (return request, eligibility, label, return scan,
refund, close); simulated store, three fictional carriers, refund service, event
bus; ScriptedAgent and LLMAgent (cassette record/replay); three runtimes:
naive, keyed (Cut 2) and hardened (state machine, effects ledger, idempotency,
reconciliation, policy engine, audit log); seeded scenario generator and fault injector;
invariant checker; CLI `eval`, `replay`, `report`, `record`; one static HTML trace
viewer (Cut 3); interactive React + TypeScript trace viewer in web/ (Vite, Vitest, Playwright)
deployed to GitHub Pages; one Stripe test-mode refund adapter run only by make demo-real;
CI workflow (.github/workflows/ci.yml) running lint, test, 200-seed eval, and hardened replay hash check; docs.

OUT OF SCOPE (do not build, scaffold or "prepare for"): HTTP servers or FastAPI;
auth, users, multi-tenancy; any chart library; other workflows (tracking,
transport, tickets, exchanges); Postgres, Redis, Kafka, Celery, Temporal or any
external service; Docker, K8s, cloud deploy (except GitHub Pages for web/);
fine-tuning, RAG, embeddings, agent frameworks (LangChain etc.); OpenTelemetry;
premature optimization; currency, tax, shipping-cost, inventory logic;
plugin systems, config frameworks, speculative abstractions; rewrites in other languages.

Scope change requests go in `docs/DECISIONS.md`. Do not implement them.

## 4. Stack (pinned)

Python 3.12, full type annotations, `mypy --strict` on `src/`. Pydantic v2.
sqlite3 (stdlib) for the ledger. PyYAML for policy and scenario config.
pytest, ruff. LLM: Gemini via Vertex AI with the `google-genai` SDK, used only
in `src/crashproof/agent/llm.py`; project, location and model come from env vars
`GCP_PROJECT`, `GCP_LOCATION`, `GEMINI_MODEL`; Application Default Credentials;
never commit keys. Concurrency: single-threaded discrete-event scheduler.
Frontend (web/): Node 20+, npm, Vite, React 18+, TypeScript (strict), Vitest, Playwright.

## 5. Cuts and gates (full detail in docs/ARCHITECTURE.md)

Cut 1 core proof (T1-T10): determinism primitives, models and trace, world with
carrier A, faults F1 F2 F3 F5, ScriptedAgent, naive and hardened runtimes with
ledger and reconciliation, invariants I1 I2 I5 I8 I9, eval and replay.
Gate 1: on 200 seeds hardened has 0 violations of I1 I2 I5 I8 I9, naive has at
least 1 violating seed, and replaying a seed twice gives an identical
trace_hash. Fault probabilities are frozen in config before the first eval.
Cut 2 (C2-1..C2-7): carriers B and C, faults F4 F6 F7 F8, policy P1-P6 with a
simulated approver, invariants I3 I4 I6 I7, keyed runtime, LLMAgent with
cassettes, 1,000-seed eval. Gate 2: hardened has 0 violations of I1-I9 on 1,000
seeds (ScriptedAgent); `make eval` and `make report` produce results/summary.json
and results/summary.md.
Cut 3 (C3-1..C3-4): trace viewer, README, DESIGN.md, demo script.
Gate 3: a fresh clone runs `make setup && make eval && make replay SEED=1` and
the viewer loads the replay JSONL.
Never start a later cut before the human approves the earlier gate.

## 6. Working protocol (every task)

1. State the cut and the task from `STATE.md` "Next task". If the request
   matches no planned task, stop and ask.
2. List the files you will touch. If any is outside the layout in
   docs/ARCHITECTURE.md, stop and ask.
3. Write the test first when practical.
4. Implement the minimum that satisfies the task.
5. Run `make test` and `make lint`. Fix, never hide.
6. Update STATE.md and append to LOG.md (Rule 16).
7. Commit on main with an imperative message.
8. Report what changed, what was tested, what remains. No claim without a
   command you actually ran.
9. Stop and wait.

Stop and ask when: requirements are ambiguous; you want to add a dependency,
file or feature not listed; an invariant, policy or fault seems wrong; a fix
would change design or scope; anything destructive is involved.

Forbidden: nice-to-have features or flags; unrelated refactors; renaming or
moving files without approval; hand-written results; claiming success without
running the verifying command; silently changing an invariant; live LLM calls
in bulk runs; starting the next task unprompted.

## 7. Code standards (summary)

Small functions, explicit types, no global mutable state, clock/rng/ids injected
as parameters, no dead or commented-out code, no TODOs without a DECISIONS.md
entry, specific exceptions (never return None for failure), structured JSON
logs with `seed`, `virtual_time`, `workflow_id`, `event`. Prefer deleting code
to adding code.

## 8. Session start (do this first, every session)

1. Read this file in full.
2. Read `STATE.md` in full.
3. Run `tail -n 60 LOG.md` (last 5 entries only).
4. Run `git status` and `git log --oneline -15`.
5. Read these only as needed for the current task:
   @docs/ARCHITECTURE.md
   @docs/SPEC.md
   @docs/INVARIANTS.md
   @docs/DECISIONS.md
6. Confirm to the human, in 3 lines: current cut, next task, anything in STATE.md
   or LOG.md that conflicts with the code.
   Do not start work until the human replies.

Reviewer sessions: when asked to review, follow `docs/REVIEW.md` on the latest
task's diff only. Report findings. Do not fix anything or edit any file.

If any document conflicts with this file, stop and ask the human.
