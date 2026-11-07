# STATE.md
<!-- Overwrite this whole file at the end of every task. Max 25 lines. -->

Cut: Frontend F1-F6 / Real adapter R1-R5
Last completed: R2 durable ledger: connect_file and open_ledger (autocommit), Ledger.close, optional ledger_path in run_seed
Next task: awaiting human: review R2 (branch feat/r2-file-ledger); then R3 Stripe adapter needs a free Stripe test-mode account and key (human decision)
Branch: feat/r2-file-ledger (based on main after R1 merge, PR #8)
Last verified: make test (726 passed), make lint clean; web and e2e not rerun this task
Byte-identity: 3000 trace hashes (naive, keyed, hardened, seeds 1..1000) and sha256 of summary.json and failures.json identical to the pre-R1 baseline; a 20-seed hardened run with a file ledger gives the same trace_hash as in-memory
Last make eval (1000 seeds), violating seeds: hardened 0, keyed 631, naive 747
Mutation tests: make mutate, 13 tests; killed M1 M2 M3 M4 M5 M7 M9 M10 by invariants, M8 by LedgerError, survive M6 M11 M12
LLM eval (3 seeds), violating seeds: hardened 0, keyed 2, naive 3 (0 malformed outputs)
Gate 3: passed; repo pushed to https://github.com/ManojDevarajulu/CrashProof
Frozen (do not touch): scenario_config.yaml, policy_rules.yaml, LLM prompt (build_prompt)
Model: gemini-2.5-flash-lite
Not built: C2-2 (F4 F6 F7 F8), R3-R5
Scope change requests pending: none (F1-F6, R1-R5 and M1 approved)
Risk: no real process kill is tested yet (R5); make test is slower on this machine today (about 50s vs 20s earlier, spread across unrelated tests)
Failed approach: none
