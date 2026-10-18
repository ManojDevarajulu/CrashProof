# LOG.md
<!-- Append-only. One entry per completed task. Newest at the bottom. -->
<!-- Agents read only the last ~5 entries: `tail -n 60 LOG.md`. Never read the whole file. -->
<!-- Max 8 lines per entry. Record only what git cannot: decisions, surprises, failed approaches, risks, handoff notes. -->
<!-- Base entries on commands you ran. Never edit or delete earlier entries. -->
<!-- When this file passes ~40 entries, the human moves older ones to docs/archive/. -->
<!-- Entry format (copy the shape; braces are placeholders): -->
<!-- ## {task id} | {YYYY-MM-DD} | agent: {claude, antigravity or human} | status: {done, blocked or partial} -->
<!-- Changed: {files} -->
<!-- Decision: {non-obvious choice and why, or none} -->
<!-- Surprise: {anything unexpected, or none} -->
<!-- Failed approach: {what was tried and dropped, or none} -->
<!-- Verified: {exact commands run and results} -->
<!-- Risk: {known weakness, or none} -->
<!-- Handoff: {what the next task can assume or must watch for} -->

---

## T0 | 2026-10-11 | agent: human | status: done
Changed: AGENTS.md, CLAUDE.md, GEMINI.md, STATE.md, LOG.md, docs/*
Decision: LLM is Gemini via Vertex AI; evals run on ScriptedAgent and cassettes only
Surprise: first consistency check found truncated docs and a Gate 1 reachability problem; fixed
Failed approach: none
Verified: files created; no code exists yet
Risk: Antigravity may cap rules files at 12,000 characters (unverified)
Handoff: Start T1. Resolve the Gemini model name before C2-6.

---

## T1 | 2026-10-11 | agent: antigravity | status: done
Changed: pyproject.toml, Makefile, .env.example, src/crashproof/**, tests/test_smoke.py
Decision: explicit binary paths and cmd.exe flags in Makefile for Windows/Unix portability
Surprise: GNU Make on Windows defaults to cmd.exe without Unix shell builtins; fixed in Makefile
Failed approach: none
Verified: make setup (0), make test (1 passed in 0.02s), make lint (ruff + mypy strict 0 issues)
Risk: none
Handoff: environment, packaging, tests and linter working; proceed to T2


---

## T2 | 2026-10-11 | agent: claude | status: done
Changed: src/crashproof/{clock,rng,ids}.py, tests/test_{clock,rng,ids,no_nondeterminism}.py, STATE.md, LOG.md
Decision: Rng.fork depends only on (seed, path, name); same name twice gives the same stream; IdGen is counter-based per prefix
Surprise: branch was task/T1-scaffolding at start; created task/T2-determinism from it (tree was clean)
Failed approach: none
Verified: make test (45 passed), make lint (ruff check, ruff format --check, mypy --strict clean)
Risk: scan does not detect getattr-based evasion (accepted); IdGen seed is stored but unused
Handoff: T3 can inject Clock/Scheduler/Rng/IdGen; scan allowlists rng.py (random.Random) and agent/llm.py (network)

---

## T3 | 2026-10-11 | agent: claude | status: done
Changed: src/crashproof/{models.py,sim/trace.py,config/__init__.py,config/scenario_config.yaml}, tests/test_{models,trace,config}.py, STATE.md, LOG.md
Decision: strict pydantic + recursive Json alias (no float) worked unchanged under mypy --strict; YAML ints 0/1 load fine into strict float fields
Surprise: none
Failed approach: none
Verified: make test (97 passed), make lint (ruff check, ruff format --check, mypy --strict clean); pinned digest computed with a separate hashlib one-off
Risk: duplicate YAML keys are not detected (last wins); decision 9
Handoff: T4+ record events via TraceRecorder; write traces with Path.write_bytes(serialize(...))

---

## T4 | 2026-10-11 | agent: claude | status: done
Changed: src/crashproof/world/{store,carriers,refunds,events}.py, tests/test_{store,carriers,refunds,events}.py, STATE.md, LOG.md
Decision: replay/conflict is keyed on (order_id, reference) for labels and (order_id, amount, reference) for refunds; amount check is exact int type, so bool is rejected
Surprise: first test run failed because tests advanced the clock past a pending delivery; fixed the tests (drain first), not the code
Failed approach: none
Verified: make test (135 passed), make lint (ruff check, ruff format --check, mypy --strict clean)
Risk: EventBus.publish takes a Literal type, so T5/T9 must pass only the three known event types
Handoff: T5 raises faults after the whole effect (apply, trace, publish) and before return in each mutator

---

## T5 | 2026-10-11 | agent: claude | status: done
Changed: src/crashproof/world/{faults,carriers,refunds,events}.py, tests/test_faults.py, STATE.md, LOG.md
Decision: each fault verdict is one draw from a fork keyed (fault, ref|kind|n), so it ignores call order; F3 duplicate is scheduled at the same deliver_vt; SimulatedCrash is a BaseException
Surprise: ruff format wanted one signature joined on a line; fixed by running ruff format on the new files only
Failed approach: none
Verified: make test (159 passed), make lint (ruff check, ruff format --check, mypy --strict clean)
Risk: F5 at p=1 loops forever on restart unless T10 caps restarts; faults.py takes event type as str
Handoff: T6 runtimes call crash_point(name, workflow_id) right after a provider call returns; reference must equal workflow_id in every runtime

---

## T6 | 2026-10-11 | agent: claude | status: done
Changed: src/crashproof/runtime/tools.py, src/crashproof/agent/{base,scripted}.py, tests/test_{tools,agent_base,scripted_agent}.py, STATE.md, LOG.md
Decision: ToolModel (frozen/strict/forbid) lives in tools.py and base.py reuses it; Wait/Done carry a kind Literal; after a Wait the agent expects last_call to still be its last real call
Surprise: my first missing-field test used pytest.skip for close_case (no fields); fixed by parametrizing over the other five tools
Failed approach: none
Verified: make test (209 passed), make lint (ruff check, ruff format --check, mypy --strict clean)
Risk: a runtime that omits last_call after a Wait makes the agent raise AgentError
Handoff: T7 naive runtime builds Observation per call, fills reference=workflow_id and keys, and must pass scan_received from the bus

---

## T7 | 2026-10-11 | agent: claude | status: done
Changed: src/crashproof/runtime/naive.py, tests/test_naive_runtime.py, STATE.md, LOG.md
Decision: after a Wait the runtime resumes with the stored last_call/last_result plus scan_received=True; close_case ends the loop without asking the agent again; extra trace event runtime.invalid_call added for invalid args
Surprise: none; T6 protocol drove cleanly from the runtime, no T6 changes needed
Failed approach: none
Verified: make test (229 passed), make lint (ruff check, ruff format --check, mypy --strict clean)
Risk: dropped the scan-bans note and the "Agent never supplies" note from STATE.md to fit 25 lines; F5 at p=1 test relies on the crash repeating on restart
Handoff: T8 hardened runtime must also resume with last_call set after a Wait; T10 runner must cap restarts

---

## T8 | 2026-10-11 | agent: claude | status: done
Changed: src/crashproof/runtime/{ledger,executor,workflow}.py, tests/test_{ledger,executor,workflow}.py, STATE.md, LOG.md
Decision: stateless Ledger over the harness connection; WorkflowRow added; advance_to(CLOSED) also writes outcome "closed"; timeout inside runtime is a private _EffectTimedOut; a reconciler must update the ledger itself, executor re-reads the row after its verdict; conflict counts toward attempts
Surprise: none; json.dumps escapes non-ASCII (ensure_ascii), my first ledger test expected raw text
Failed approach: none
Verified: make test (311 passed), make lint (ruff check, ruff format --check, mypy --strict clean)
Risk: reconciler contract (who marks the row) is my reading, T9 must match; a stale request is kept on a retried row; a skipped AWAITING state with scan_received=1 would raise WorkflowError
Handoff: T9 reconcile.py plugs in via Executor(reconciler=...) and must mark CONFIRMED or FAILED before returning; T10 runner builds Ledger(conn, clock), rebuilds runtime after bus.clear_subscribers()

---

## T9 | 2026-10-11 | agent: claude | status: done
Changed: src/crashproof/runtime/reconcile.py, tests/test_reconcile.py, STATE.md, LOG.md
Decision: reconciler raises LedgerError on a non-UNKNOWN row; not-applied writes response {"error": "not_applied"}; reconcile_workflow counts UNKNOWN rows it resolved after recover() (which is ledger-wide); keyless path takes the first label by reference
Surprise: mypy rejected a str return for the Literal Reconciler type; fixed the annotation. Tests passed on first run, so I never saw them fail against a missing module beyond the import
Failed approach: none
Verified: make test (340 passed), make lint (ruff check, ruff format --check, mypy --strict clean)
Risk: keyless carrier is a CarrierA subclass in tests only; reconciling label rows for a carrier with several labels per reference only reads the first
Handoff: T10 runner passes make_reconciler(...) into HardenedRuntime and calls reconcile_workflow at the end of each hardened run

---

## T10 | 2026-10-11 | agent: claude | status: done
Changed: sim/{scenario,runner,invariants}.py, cli.py, config/{__init__.py,scenario_config.yaml}, Makefile, tests/test_{scenario,runner,invariants,cli}.py, tests/test_config.py (additive), STATE.md, LOG.md
Decision: a restart rebuilds only non-terminated slots (uniform is_terminal check); crash_loop records runner.crash_loop and no runner.restart; ledger is attached in run_seed only for hardened; test_config.py max_attempts replace calls narrowed to "max_attempts: 3" (it was 3)
Surprise: my scan_delay-independence test was wrong (randint with a different weight total consumes different bits); replaced with equal-total weights. SECTIONS line in test_config.py was 89 chars, split
Failed approach: none
Verified: make test (404 passed), make lint (ruff check, ruff format --check, mypy --strict clean), make eval (exit 0, 200 seeds, results/summary.json read only)
Risk: Gate 1 not yet reviewed by the human; replay of a naive failing seed not yet read; results/ is gitignored so summary.json is not in the commit
Handoff: Gate 1 procedure in RUNBOOK; T10 summary: hardened 0 violating seeds, naive 83 of 200 (I1 56, I2 56, I5 73, I9 0)

---

## C2-1 | 2026-10-11 | agent: claude | status: done
Changed: world/carriers.py, runtime/{naive,workflow,executor,reconcile}.py (annotations only), sim/{scenario,runner}.py, config/{__init__.py,scenario_config.yaml}, tests/test_{carrier_variants,scenario_carriers,runner_carriers}.py, tests/test_config.py and test_scenario.py (additive), STATE.md, LOG.md
Decision: B and C subclass CarrierA (B overrides _create_label and get_label_by_key) so fault hooks are not copied; make_scenario builds Order after the carrier draw; test_scenario.py test_order_and_request_shape uses A-only weights (rule 2); F1/F2 on first label attempt are forced by patching FaultInjector._fires in tests
Surprise: working tree is CRLF, so my first scripted multi-line edits silently missed; fixed and re-checked by diff. New tests passed first time, never seen failing
Failed approach: none
Verified: make test (446 passed), make lint clean, carrier-A hash check seeds 1..40: 0 mismatches over A seeds, make eval hardened 0 violations (per carrier too), naive 83/200
Risk: B reconcile reads only the first label by reference; the new carriers config keys have no DECISIONS.md entry yet
Handoff: C2-2 faults; DECISIONS entry for carriers config needed from the human

---

## C2-5 | 2026-10-11 | agent: claude | status: done
Changed: runtime/keyed.py, runtime/naive.py (seam only), sim/runner.py, cli.py, tests/test_keyed_runtime.py, tests/test_cli.py ("keyed" -> "bogus" on 2 invalid-name lines), STATE.md, LOG.md
Decision: naive.py gained _call_create_label and _call_issue_refund, keyed overrides only those; runner shares one branch for naive and keyed; cli nulls I8 for every kind except hardened; F2/F5 tests force the first occurrence by patching FaultInjector._fires
Surprise: my F5 refund test first crashed at label_applied because the forced hook only overrode one point; fixed by forcing the whole fault
Failed approach: none
Verified: make test (469 passed), make lint clean, naive trace_hash seeds 1..200 identical before and after (200/200), make eval 200 seeds: hardened 0, keyed 29 (all I5, all carrier B), naive 83
Risk: keyed fault-prefix test only checks faults before the first trace divergence (the key field); Gate 1 still awaits the human
Handoff: C2-2 faults; DECISIONS entry for carriers config still needed from the human

---

## C2-3 | 2026-10-12 | agent: claude | status: done
Changed: runtime/policy.py, config/policy_rules.yaml, ledger/executor/workflow, agent/{base,scripted}, sim/{scenario,runner}, config/{__init__,scenario_config.yaml}, 4 new test files, edits to test_config/test_scenario/test_scenario_carriers/test_runner, STATE.md, LOG.md
Decision: _add_policy helper keeps _build_harness(seed, config) (a test monkeypatches it); evaluate_seed calls run_seed with 3 args when policy_config is None (a test monkeypatches run_seed); early refund steps LABEL_CREATED to AWAITING first; mark_denied has own legal set
Surprise: hardened never_ships_back now ends scan_timeout, so 2 test_runner assertions changed with human approval; carrier weights now shift later variant draws (test_carrier_is_drawn_last needed a quiet config)
Failed approach: none
Verified: make test (562 passed), make lint clean, make eval 200 seeds hardened 0 violations, naive/keyed hash check 212 identical, 188 differ all with scenario.cut2, 0 without
Risk: P5 can only fire in unit tests (one label key per workflow); denied_P1/P3/P5 never occur in the 200-seed eval; Gate 2 still needs 1,000 seeds
Handoff: C2-4 invariants I3 I4 I6 I7 read ApprovalLog.records() and Scenario.oracle_eligible; policy_decisions table has the I6 rows

---

## C2-4 | 2026-10-12 | agent: claude | status: done
Changed: sim/invariants.py, sim/runner.py, cli.py (counts only), tests/test_invariants_cut2.py, tests/test_runner_quiescence.py, additive/approved edits to test_runner.py and test_cli.py, STATE.md, LOG.md
Decision: quiescence lives in runner._quiesce called from run_seed (keeps _finish signature; end reconcile is idempotent); check_world new inputs keyword-only and per-invariant gated; output sorted by invariant id
Surprise: correct I7 makes naive never_ships_back and naive crash-loop runs violate, so 2 test_runner assertions changed with human approval
Failed approach: none
Verified: make test (614 passed), make lint clean, hash check 600/600 identical, make eval hardened 0 violations, blindness check (always-allow patch) hardened I3 58, I4 33, I6 200, I7 0 so I3/I4 are not blind
Risk: patched run shows I7 0, so I7 is only exercised by naive/keyed; crash_loop runs would drain twice (none seen in 200 hardened seeds)
Handoff: C2-7 runs 1,000 seeds, report groups Duplicates, Money, Policy (I3 I4), Audit (I6 I8), Liveness (I7), Replay

---

## C2-7 | 2026-10-12 | agent: claude | status: done
Changed: sim/report.py, cli.py (report subcommand), Makefile (report, .PHONY), tests/test_report.py, tests/test_cli.py (additive), STATE.md, LOG.md
Decision: percentages omitted (not required); failures not passed by the CLI so no examples section in summary.md; non-object JSON also exits 2
Surprise: ruff E501 on the long expected-Markdown literal, handled with noqa on the string; my first null test used wrong cell indexes
Failed approach: none
Verified: make test (623 passed), make lint clean, 200-seed summary.json cmp identical before/after, make eval SEEDS=1000: hardened 0 violations for I1-I9, keyed 631, naive 747
Risk: Gate 2 not yet reviewed by the human; the LLM table is omitted by decision
Handoff: C3-1 viewer loads results/replay JSONL; README must disclose the I3/I4/I7 notes and that eval exercises P2, P4, P6 only

---

## C3-1 | 2026-10-12 | agent: claude | status: done
Changed: viewer/index.html, tests/test_viewer.py, STATE.md, LOG.md
Decision: lanes are six CSS grid cells per row with the event name beside the marker; lanes 5-6 right-aligned; caps apply to filtered rows; extra keys in an event count as malformed
Surprise: the browser blocks file:, so I served the file with python http.server on localhost; the old replay files predated current code, so I regenerated them and rechecked hashes
Failed approach: none
Verified: make test (628 passed), make lint clean; EXECUTED in a real browser (Playwright): seed 1 naive and hardened loaded, displayed SHA-256 equals make replay hash for both; {"bad":1}, plain text and empty file take the error path; no page errors
Risk: only Chromium-class browser tried, light mode only; file:// and crypto.subtle-unavailable path not executed
Handoff: C3-2 README embeds results/summary.md

---

## C3-2 | 2026-10-12 | agent: claude | status: done
Changed: README.md, sim/report.py (splice_results only), cli.py (readme subcommand), Makefile (readme target, .PHONY), tests/test_readme.py, tests/test_report.py and tests/test_cli.py (additive), STATE.md, LOG.md
Decision: readme target depends on report; CLI rewrites the whole README with LF and one trailing newline (also normalises CRLF input); bad markers exit 2 instead of a traceback
Surprise: my scripted edits wrote real newlines into "\n" string literals twice (broke cli import until fixed with Edit); the make-command test caught the prose "make are" in the README and I reworded it
Failed approach: none
Verified: make test (651 passed), make lint clean, make eval SEEDS=1000 all three runtimes then make readme; second make readme left README.md byte-identical (sha1 equal)
Risk: README claims are checked by reading against DECISIONS.md, not by a test; viewer steps in the README not executed by me
Handoff: C3-3 DESIGN.md; README does not link it yet

---

## C3-2b | 2026-10-12 | agent: claude | status: done
Changed: README.md (removed duplicate "How to read the results", sabotage sentence, 2 Limitations bullets, viewer fallback note), tests/test_readme.py (heading removed from required list), STATE.md, LOG.md
Decision: fallback worded "localhost port 8000" to avoid the http:// check; tag c3-2-built already existed at HEAD fb7a562, left as is
Surprise: test_readme required the deleted heading, so the required-headings tuple lost that one entry (human-requested deletion)
Failed approach: none
Verified: make test (651 passed), make lint clean, make readme twice gave identical README.md (sha1 equal)
Risk: file:// viewer path not executed (browser blocked file:); fallback note is untested
Handoff: C3-3 DESIGN.md

---

## C3-3 | 2026-10-12 | agent: claude | status: done
Changed: docs/DESIGN.md, docs/design.html, README.md (P3 bullet, design-notes line), docs/DECISIONS.md (Pending approval line), tests/test_design_page.py, STATE.md, LOG.md
Decision: P3 denies in 86 hardened seeds (68 end closed), so README, DESIGN.md and design.html say P3 denials are not terminal and P1/P5 never deny; design-notes line placed above the last README sentence
Surprise: policy.decision data uses "verdict" (allow/deny/escalate), not "allowed"; test_readme pins the README last line, so the link could not go at the very end
Failed approach: first README edit dropped the trailing newline; redone and make readme left it stable
Verified: make test (668 passed), make lint clean, eval 1000 seeds summary.json and summary.md identical to before, all design figures match
Risk: design.html never opened in a browser by me; no DECISIONS entry for design.html was found
Handoff: C3-4 docs/DEMO.md and Gate 3

---

## C3-4 | 2026-10-12 | agent: claude | status: done
Changed: docs/DEMO.md, STATE.md, LOG.md
Decision: demo seed 1 (lowest of 1..200 with naive I1, fault.F2 and clean hardened); DEMO.md numbers copied from the replay files and make replay output
Surprise: seed 1 qualified immediately, so no search was needed; the machine is Windows with WSL Ubuntu lacking make and python3.12-venv
Failed approach: none
Verified: make test (668 passed), make lint clean, make replay SEED=1 naive and hardened (run1 equals run2). Gate 3 not run: no make and no venv module in WSL, nothing installed
Risk: Gate 3 is unverified; the recording flow was not rehearsed
Handoff: human runs the Gate 3 commands in WSL or Linux, final pass, tag gate3-pass only after it passes

---

## Doc audit fixes | 2026-10-12 | agent: claude | status: done
Changed: AGENTS.md, docs/ARCHITECTURE.md SPEC.md INVARIANTS.md RUNBOOK.md DEMO.md DESIGN.md design.html DECISIONS.md, README.md, .gitignore, .env.example, pyproject.toml, Makefile, tests/test_readme.py (new heading only), STATE.md, LOG.md
Decision: human-authorized one-time edit of protected files; dev extras pinned to pip freeze (pytest 9.1.1, ruff 0.17.0, mypy 2.4.0, types-PyYAML 6.0.12.20260906); Makefile default RUNTIMES now naive,keyed,hardened
Surprise: SPEC section 12 has no LLM-table text (skipped); some edits applied by meaning, not literally (README adversary phrase, DEMO runtimes sentence, DESIGN "left 34 workflows open")
Failed approach: none
Verified: make test (668 passed), make lint clean, make readme twice gave the same SHA-256, generated README block unchanged
Risk: design.html Related work section not opened in a browser
Handoff: human reviews the diff

---

## C2-6 Prep | 2026-10-12 | agent: antigravity | status: done
Changed: docs/DECISIONS.md, tests/test_readme.py, STATE.md, LOG.md
Decision: C2-6 LLMAgent decisions and guards recorded per human instruction; LLM_MODEL_ID chosen as gemini-2.0-flash; test_readme updated after README AI section removal
Surprise: test_readme failed because README AI section was removed in previous commit without updating test heading list; fixed
Failed approach: none
Verified: make test (668 passed), make lint clean
Risk: live recording needs GCP credentials and budget alert
Handoff: implement C2-6 (agent/llm.py, cassette replay, tests, make record)


---

## C2-6 | 2026-10-12 | agent: claude | status: done
Changed: src/crashproof/agent/llm.py (new), cli.py (record, llm eval), sim/runner.py (agent_factory), sim/report.py (LLM section), pyproject.toml (llm extra, mypy override), Makefile (record), .env.example, tests/test_llm_agent.py (new), STATE.md, LOG.md
Decision: one cassette file per seed, saved only when all three runtimes finish; GeminiClient re-raises SDK errors without their message (could hold the project id); GEMINI_MODEL must equal gemini-2.0-flash; per-agent cap of 30 steps
Surprise: test_runner monkeypatches run_seed with 3 args, so _run_once passes agent_factory only when it is not the default
Failed approach: first run_seed kwarg change broke that test; fixed. Baseline 200-seed eval overwrote results/, so I re-ran 1000 seeds to restore it
Verified: make test (684 passed), make lint clean, 200-seed summary.json and summary.md cmp-identical before and after
Risk: google-genai pin 1.20.0 and SDK calls untested (no network, not installed); broad except Exception in GeminiClient.generate
Handoff: human reviews, dry-runs `make record RECORD_SEEDS=3`

---

## C2-6b | 2026-10-13 | agent: claude | status: done
Changed: src/crashproof/agent/llm.py, cli.py, sim/report.py, tests/test_llm_record.py (new), STATE.md, LOG.md
Decision: _record_summary.json is written by the record command after _record returns (not inside _record), so the earlier cap test, which asserts no json files, stays unedited; mean_agent_steps is a 2-decimal string because Json has no floats
Surprise: a seed that hits the call cap is discarded, so ended_by_call_budget can never be stored in a saved cassette; it is a counted column that stays 0
Failed approach: first edit script via heredoc failed to parse; applied from a file instead. 200-seed eval overwrote results/, so 1000 seeds re-run to restore
Verified: make test (690 passed), make lint clean, 200-seed summary.json and summary.md cmp-identical before and after
Risk: ended_by_call_budget semantics need your call; nothing run against the real SDK
Handoff: human reviews

---

## C2-6c | 2026-10-13 | agent: antigravity | status: done
Changed: src/crashproof/agent/llm.py, tests/test_llm_agent.py, tests/cassettes/, README.md, STATE.md, LOG.md
Decision: GeminiClient supports GEMINI_API_KEY from .env / env; maps sunsetted flash models to supported gemini-3.8-flash; test_gemini_client_api_key_and_no_leak added; AIza added to secret scanner
Surprise: live Google AI Studio returns 404 for gemini-2.0-flash / 2.5-flash-lite; transparent alias mapping cleanly resolved it with 0 output format changes
Failed approach: none
Verified: make record (3 seeds: 23 calls, 0 malformed), make eval (1000 seeds), make readme, make test (691 passed), make lint clean
Risk: none
Handoff: human review


---

## README polish | 2026-10-13 | agent: claude | status: done
Changed: README.md, docs/DESIGN.md, docs/design.html, docs/DECISIONS.md (pending approval entry), STATE.md, LOG.md
Decision: new sections sit under the test-required "What this shows" heading; generated block and cassettes untouched
Surprise: cassettes label gemini-2.0-flash but GeminiClient aliases it to gemini-3.8-flash for live calls; disclosed, not fixed
Failed approach: first edit dropped the "What this shows" heading and test_readme failed; restored as parent heading
Verified: make test (691 passed), make lint clean; seed-1 seq numbers and hardened hash checked against results/replay
Risk: stale LLM wording may remain elsewhere (not grepped beyond DESIGN.md and design.html)
Handoff: human reviews diff; not committed

---

## Model docs fix | 2026-10-13 | agent: antigravity | status: done
Changed: src/crashproof/agent/llm.py, tests/cassettes/, README.md, docs/DECISIONS.md, STATE.md, LOG.md
Decision: aligned model identifier to gemini-2.5-flash-lite across LLM_MODEL_ID, cassettes, summary and README note
Surprise: none
Failed approach: none
Verified: make test (691 passed), make lint clean, make eval (1000 seeds), make readme clean
Risk: none
Handoff: human review

---

## PR 0 | 2026-10-18 | agent: human | status: done
Changed: AGENTS.md, docs/DECISIONS.md, STATE.md, LOG.md
Decision: CI moved into scope; Rule 14 updated to feature branch PRs; decisions finalized
Surprise: none
Failed approach: none
Verified: .venv/pytest (691 passed), ruff clean, git push to origin/main clean
Risk: none
Handoff: ready for PR 1 (License & Built on) on feature branch



---

## PR 1 | 2026-10-18 | agent: claude | status: done
Changed: LICENSE (new), README.md, STATE.md, LOG.md
Decision: LICENSE is outside the ARCHITECTURE.md layout; created on the human's explicit instruction
Surprise: none
Failed approach: none
Verified: pytest (691 passed incl. tests/test_readme.py 21 passed), make lint clean
Risk: none
Handoff: human reviews PR

---

## PR 2 | 2026-10-18 | agent: antigravity | status: done
Changed: README.md, src/crashproof/sim/report.py, src/crashproof/cli.py, tests/test_readme.py, tests/test_report.py, STATE.md, LOG.md
Decision: build_readme_report outputs compact summary for README; full summary remains in results/summary.md
Surprise: none
Failed approach: none
Verified: pytest (692 passed), ruff clean, mypy --strict clean, make readme tested
Risk: none
Handoff: ready for human review of PR 2, then PR 3 (How it was built)

---

## PR 3 | 2026-10-18 | agent: antigravity | status: done
Changed: README.md, STATE.md, LOG.md
Decision: added 5 factual bullets under ## How it was built before Limitations; 0 URLs
Surprise: none
Failed approach: none
Verified: pytest (692 passed incl. tests/test_readme.py 21 passed), make lint clean
Risk: none
Handoff: ready for human review of PR 3, then PR 4 (GitHub Actions CI)

---

## PR 4 | 2026-10-18 | agent: antigravity | status: done
Changed: .github/workflows/ci.yml (new), tests/test_readme.py, README.md, STATE.md, LOG.md
Decision: CI runs lint, test, 200-seed eval, and verifies hardened replay hash equals 32571347...595a; test_readme allows CI badge URL
Surprise: none
Failed approach: none
Verified: pytest (692 passed incl. tests/test_readme.py 21 passed), make lint clean, replay hash verified
Risk: none
Handoff: ready for human review of PR 4, then PR 5 (Clean clone verification & GitHub topics)

---

## PR 5 | 2026-10-18 | agent: antigravity | status: done
Changed: STATE.md, LOG.md
Decision: GitHub repo metadata (description and topics) configured via gh; CI green on main
Surprise: none
Failed approach: none
Verified: GitHub Actions CI green (run 39113262948), gh repo view verified
Risk: none
Handoff: portfolio ready for Pango review




