# STATE.md
<!-- Overwrite this whole file at the end of every task. Max 25 lines. -->

Cut: Frontend F1-F6 / Real adapter R1-R5
Last completed: R3 StripeRefunds adapter (src/crashproof/adapters/stripe_refunds.py), tested only against a fake transport
Next task: awaiting human: review R3 (branch feat/r3-stripe-adapter); R4 needs a free Stripe test-mode account and sk_test_ key to RECORD real responses (human runs the recording)
Branch: feat/r3-stripe-adapter (based on main after R2 merge, PR #9)
Last verified: make test (766 passed), make lint clean; web and e2e not rerun this task
Byte-identity: 3000 trace hashes (naive, keyed, hardened, seeds 1..1000) and sha256 of summary.json and failures.json identical to the pre-R1 baseline; no src module imports the adapter
Last make eval (1000 seeds), violating seeds: hardened 0, keyed 631, naive 747
Mutation tests: make mutate, 13 tests; killed M1 M2 M3 M4 M5 M7 M9 M10 by invariants, M8 by LedgerError, survive M6 M11 M12
LLM eval (3 seeds), violating seeds: hardened 0, keyed 2, naive 3 (0 malformed outputs)
Gate 3: passed; repo pushed to https://github.com/ManojDevarajulu/CrashProof
Frozen (do not touch): scenario_config.yaml, policy_rules.yaml, LLM prompt (build_prompt)
Model: gemini-2.5-flash-lite
Not built: C2-2 (F4 F6 F7 F8), R4, R5
Scope change requests pending: none (F1-F6, R1-R5 and M1 approved)
Risk: the adapter has NEVER talked to Stripe. Its error mapping (idempotency_error, param=amount, 429/5xx as unknown) and metadata-filter lookup are from Stripe's documented behaviour, unverified until R4 recordings and R5. FakeStripe in tests is our model, not a recording
Risk: created_vt carries Stripe epoch seconds, not virtual time
Failed approach: first appended scan-test code had literal newlines in string literals (heredoc escaping); rewritten
