# STATE.md
<!-- Overwrite this whole file at the end of every task. Max 25 lines. -->

Cut: Frontend F1-F6 / Real adapter R1-R5
Last completed: R1 RefundProvider protocol in world/refunds.py; executor, naive, reconcile and workflow depend on it
Next task: awaiting human: review R1 (branch feat/r1-refund-provider); then R2 file-backed ledger (plan agreed: open_ledger with autocommit, optional ledger_path in run_seed)
Branch: feat/r1-refund-provider (based on main after M1 merge, PR #7)
Last verified: make test (721 passed), make lint clean; web and e2e not rerun this task
Byte-identity: trace_hash for naive, keyed, hardened on seeds 1..1000 (3000 hashes) identical before and after; sha256 of summary.json and failures.json from a 1000-seed eval identical
Last make eval (1000 seeds), violating seeds: hardened 0, keyed 631, naive 747
Mutation tests: make mutate, 13 tests (M1 merged); killed M1 M2 M3 M4 M5 M7 M9 M10 by invariants, M8 by LedgerError, survive M6 M11 M12
LLM eval (3 seeds), violating seeds: hardened 0, keyed 2, naive 3 (0 malformed outputs)
Gate 3: passed; repo pushed to https://github.com/ManojDevarajulu/CrashProof
Frozen (do not touch): scenario_config.yaml, policy_rules.yaml, LLM prompt (build_prompt)
Model: gemini-2.5-flash-lite
Not built: C2-2 (F4 F6 F7 F8), R2-R5
Scope change requests pending: none (F1-F6, R1-R5 and M1 approved)
Risk: none; protocol error contract (InvalidRefundAmount, IdempotencyConflict, faults.Timeout) is documented but only the simulated service and a test fake implement it
Failed approach: first protocol edit missed the import because refunds.py had CRLF endings; normalised to LF
