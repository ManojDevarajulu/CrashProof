# STATE.md
<!-- Overwrite this whole file at the end of every task. Max 25 lines. -->

Cut: Frontend F1-F6 / Real adapter R1-R5 / Mutation testing M1
Last completed: M1 mutation tests: tests/test_mutants.py, make mutate, CI step
Next task: awaiting human: review M1 (branch feat/m1-mutation); F1-F5 still awaiting merge, Pages not yet enabled, README URL exception not yet approved
Branch: feat/m1-mutation (based on main)
Last verified: make test (716 passed), make lint clean, make mutate (13 passed in 13s); web and e2e not rerun this task
Mutants (hardened, seeds 1..300): killed M1 M2 M3 M4 M5 M7 M9 M10 by invariants, M8 by LedgerError; survive M6 M11 M12 (documented in SURVIVORS)
Last make eval (1000 seeds), violating seeds: hardened 0, keyed 631, naive 747
LLM eval (3 seeds), violating seeds: hardened 0, keyed 2, naive 3 (0 malformed outputs)
Gate 3: passed; repo pushed to https://github.com/ManojDevarajulu/CrashProof
Frozen (do not touch): scenario_config.yaml, policy_rules.yaml, LLM prompt (build_prompt)
Cassettes: tests/cassettes/ (seed-0001..0003.json + _record_summary.json)
Model: gemini-2.5-flash-lite
Not built: C2-2 (F4 F6 F7 F8), R1-R5
Scope change requests pending: none (M1 appended to DECISIONS.md and approved by human)
Risk: M10 is killed by 1 of 300 seeds, so it is a thin kill; M8 is caught by a ledger guard, not an invariant
Note: src/ unchanged by M1
Failed approach: first M2 changed the ledger key per call and crashed the harness (illegal workflow transition); replaced with a per-attempt provider key
