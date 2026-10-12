# DECISIONS.md

## Decisions
- LLM: Gemini via Vertex AI (google-genai SDK); model name via GEMINI_MODEL, chosen before C2-6.
- LLM determinism comes from cassettes, not temperature. Gate 2 uses ScriptedAgent.
- Concurrency: single-threaded discrete-event scheduler, no asyncio.
- Ledger: sqlite3 (stdlib), in-memory, one database per seed.
- Runtimes: naive and hardened in Cut 1; keyed added in Cut 2 to answer "just use idempotency keys".
- Reconciliation and I9 moved into Cut 1 because Gate 1 depends on them.
- Fault probabilities are frozen in config before the first eval; later changes need an entry here.
- Rule 4 exemptions: rng.py may use random.Random(seed); agent/llm.py is the only file allowed network I/O and runs only under `make record`.
- Carriers are fictional, not modeled on real providers.
- Oracle verdict is ground truth for I3; runtimes never see it.
- Agents may append to this file under "Scope change requests" and "Pending approval" only.
- The runner (harness) owns Clock, Scheduler, Rng and IdGen; they survive simulated crashes. Only runtime objects are rebuilt from the ledger.
- Development is on Windows with LF enforced via .gitattributes; the Unix Makefile branch is untested until Gate 3.
- Single-branch workflow: commits go to main, and the human tags each reviewed task.
- T10 adds the config sections timing, run and behavior_weights, frozen before the first eval; restarts are capped per seed by run.max_restarts.
- Gate 1 sabotage: skipping the final reconcile_workflow produced 0 hardened violations in 200 seeds, so in Cut 1 the end-of-run reconcile never changes an outcome; re-test after C2-1 and C2-3.
- C2-5: keyed = naive plus the deterministic key from executor.idempotency_key(), sent to every provider that supports keys (carrier B excluded via supports_keys, the refund service always). No ledger, reconcile, policy or state machine. Whatever keyed scores is reported as measured and never tuned.
- C2-5 may add a seam to naive.py (two provider-call methods) only if every naive trace for seeds 1..200 stays byte-identical.
- C2-3 config, frozen before the Gate 2 eval and never tuned: timing.scan_timeout 172800; cut2.variant_weights {none 6, outside_window 1, high_value_over_cap 1, fraud_flagged 1, partial_return 1}; cut2.early_refund_percent 10; cut2.high_value_unit_price {min 20001, max 40000}; policy_rules.yaml: refund_cap 20000, window_days 30, approver {delay_min 3600, delay_max 86400, approve_weight 7, deny_weight 3}.
- The runtime (hardened) triggers the SimulatedApprover on a P2 escalation; escalate_to_human stays unsupported. SPEC §8 is superseded on this point.
- Policy decisions are stored in a ledger-local PolicyDecisionRow with an int id; models.PolicyDecision is unchanged and unused.
- Approver denials are not violations. An approver deny on an eligible order simply means no refund.
- Gate 1 numbers are superseded by the Cut 2 behaviors; the gate1-pass tag preserves them.
- C2-2 (faults F4, F6, F7, F8) is deferred. Nothing in C2-3, C2-4 or C2-7 depends on it.
- C2-3 eval coverage (200 seeds): hardened outcomes closed 128, denied_P2 7, denied_P4 20, denied_P6 22, scan_timeout 23. Rules P1, P3 and P5 produce no hardened denial in the eval; they are expected (P5: one label key per workflow; P1: agent never over-asks for a normal return; P3: covered by early-refund path, checked in unit tests). The README must not claim eval-measured results for P1, P3 or P5; state which rules the eval exercises (P2, P4, P6 plus P3 through the early-refund case) and that the others are covered only by unit tests. Keyed fails I5 only (carrier B).
- The note about evaluate_seed and the monkeypatched test is fine; it is a harmless compatibility choice.
- I3: a refund is valid only if a delivered return.scanned event for its workflow precedes its refund by trace seq AND the oracle verdict is eligible. I4: a refund above refund_cap needs an approved ApprovalLog record for its workflow whose approval.decided event precedes the refund by trace seq. I6 (hardened only): every CONFIRMED effect has a policy_decisions row that is allowed and belongs to the same workflow. I7: after the main drain, the runner reconciles (hardened), advances virtual time 72 hours with a no-op event, drains again, and any workflow still non-terminal is stuck.
- I7 terminal status for hardened is read from the ledger; for naive and keyed the only record is the runtime’s own is_terminal, so it is a self-report, used for I7 alone and disclosed in the README.
- Naive and keyed have no scan timeout, so a never_ships_back workflow stays open and counts as an I7 violation. This follows from the absence of a timeout, not from faults, and the README must say so.
- I3, I4 and I7 failures by naive and keyed are expected. They are measured, never tuned, and approver denials are not violations.
- C2-4: two T10 runner tests asserted that naive has no I7 violation in a stuck workflow. With invariant I7 they now expect it: naive after a crash loop stays unfinished (I5 plus I7), and naive never_ships_back stays open (I7 only). Hardened expectations are unchanged. The naive and keyed I7 failures come from having no scan timeout and no persisted state, not from faults, and the README must say so.
- Gate 2 (1,000 seeds, seeds 1..1000): hardened 0 on I1-I9. Keyed 631 violating seeds (I3 286, I4 176, I5 112, I7 160). Naive 747 (I1 214, I2 208, I3 286, I4 176, I5 277, I7 160). Hardened outcomes: closed 614, denied_P2 54, denied_P4 98, denied_P6 102, scan_timeout 132. The README table is generated from results/summary.md only.
- Pre-demo sabotage run (300 seeds, hardened): S1 refund key removed gives I1 19, I2 19, I8 189. S2 attempt-dependent key gives I1 19, I2 19, I5 15, I8 189. S3 P2 never escalates gives I4 46. S4 P6 always allows gives I3 32. S5 no scan timeout gives I7 34. S6 skipping the end-of-run reconcile gives 0 (also 0 at Gate 1): the end-of-run reconcile is a safety net that the eval does not exercise, and the README must not claim it changes outcomes.
- README claims are limited to: what make eval measured (1,000 seeds, simulated providers), and the stated caveats. It must disclose: naive and keyed have no policy engine or scan timeout by design; keyed’s only differences from hardened on the carrier A and C refund path are policy and liveness; keyed fails I5 only on carrier B; I7 for naive and keyed is a self-report; hardened’s I7 and the end-of-run reconcile are never exercised by the eval; rules P1, P3 and P5 produce no hardened denial in the eval; C2-2 faults (F4, F6, F7, F8) and the LLM agent were not built.
- docs/DESIGN.md is drafted by Opus from the code and docs, then rewritten by the human. The agent’s draft is a starting point and is never committed as the final text.
- docs/design.html and docs/DESIGN.md were drafted by a Claude agent from the repository and reviewed by the human. They are static documents. Figures in design.html are typed from results/summary.md and must be re-checked after any re-run of make eval. design.html is outside the original layout and is approved by the human.
- C2-6 (LLM agent) is built after all. Cassettes live in tests/cassettes/, shared across runtimes, keyed by (seed, step, prompt_hash). google-genai is an optional pinned extra named llm. Malformed model output counts as a failed step and ends the run. No re-asks.
- The LLM prompt is identical across runtimes and never shows keys, ledger state or the runtime name. The prompt is frozen after a 3-seed format dry run; changing it afterwards needs a new entry and a full re-record.
- make record is run by the human only, on at most 50 seeds, with a hard call cap. LLM results are reported in a separate table and are never mixed into the ScriptedAgent figures. They come from a single recorded run of one model and are not a statistical estimate. summary.json and the ScriptedAgent part of summary.md must stay byte-identical to the current ones.
- LLM_MODEL_ID: gemini-2.0-flash.

## Scope change requests
(none)

## Pending approval
- Correction (C3-3 step 0, hardened, seeds 1..1000): P3 denies in 86 seeds (of which 68 end closed, 13 scan_timeout, 5 denied_P2), so P3 denials are not terminal; P1 and P5 deny in 0 seeds. The README Limitations bullet saying P1, P3 and P5 produce no hardened denial is corrected accordingly; the earlier 200-seed note in the Decisions section is not edited.
- Correction: docs/DESIGN.md and docs/design.html as committed were drafted by a Claude agent and edited by the human; the earlier entry saying an agent draft is never committed as the final text is superseded. The README-claims entry's clause that P1, P3 and P5 produce no hardened denial is superseded by the P3 correction: P3 denies in 86 seeds; P1 and P5 never deny. Pre-send audit fixes were applied in one commit.
