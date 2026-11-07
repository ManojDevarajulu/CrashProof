# STATE.md
<!-- Overwrite this whole file at the end of every task. Max 25 lines. -->

Cut: Frontend F1-F6 / Real adapter R1-R5
Last completed: R4a Stripe recorder: make record-stripe, RecordingTransport, record_contract (tested against a fake only)
Next task: awaiting human: (1) merge R3 first, this branch is stacked on it; (2) run `make record-stripe` once with STRIPE_API_KEY set to a test key; (3) then R4b: contract tests from tests/contracts/stripe_refunds.json
Branch: feat/r4-stripe-recorder (stacked on feat/r3-stripe-adapter, PR for R3 not merged yet)
Last verified: make test (778 passed), make lint clean; web and e2e not rerun this task
Byte-identity: 3000 trace hashes (naive, keyed, hardened, seeds 1..1000) and sha256 of summary.json and failures.json identical to the pre-R1 baseline
Last make eval (1000 seeds), violating seeds: hardened 0, keyed 631, naive 747
Mutation tests: make mutate, 13 tests; killed M1 M2 M3 M4 M5 M7 M9 M10 by invariants, M8 by LedgerError, survive M6 M11 M12
LLM eval (3 seeds), violating seeds: hardened 0, keyed 2, naive 3 (0 malformed outputs)
Gate 3: passed; repo pushed to https://github.com/ManojDevarajulu/CrashProof
Frozen (do not touch): scenario_config.yaml, policy_rules.yaml, LLM prompt (build_prompt)
Model: gemini-2.5-flash-lite
Not built: C2-2 (F4 F6 F7 F8), R4b, R5
Scope change requests pending: none (R4 recorder entry appended to DECISIONS.md; human may veto)
Risk: the adapter has still NEVER talked to Stripe; no tests/contracts/stripe_refunds.json exists yet. Error mapping and lookups are unverified until the recording is made
Risk: recordings keep status and body only, no response headers, so the Stripe API version is not captured
Failed approach: first recorder swallowed a failed PaymentIntent setup as a step outcome and carried on with an empty id; caught by a test, setup now aborts
