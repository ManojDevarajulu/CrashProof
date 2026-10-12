# STATE.md
<!-- Overwrite this whole file at the end of every task. Max 25 lines. -->

Cut: 2 / 3
Last completed: C2-6 LLMAgent, cassettes, make record (code only; nothing recorded)
Next task: human review; then human runs `make record` (max 50 seeds, 600 call cap)
Branch: main
Last verified: make test (684 passed), make lint clean
200-seed eval before and after C2-6: summary.json and summary.md byte-identical (cmp)
Last make eval (1000 seeds), violating seeds: hardened 0, keyed 631, naive 747
Gate 3: passed in clean WSL clone; repo pushed to https://github.com/ManojDevarajulu/CrashProof
Frozen (do not touch): scenario_config.yaml, policy_rules.yaml, LLM prompt (build_prompt)
Cassettes: tests/cassettes/ (empty), one seed-NNNN.json per seed, shared by all runtimes
LLM table: summary.json key "llm" and a separate summary.md section, only if cassettes exist
google-genai==1.20.0 is the optional extra "llm"; lazy import; never installed or run by me
Model: gemini-2.0-flash (GEMINI_MODEL must equal it or record refuses)
Not built: C2-2 (F4 F6 F7 F8)
Scope change requests pending: none
Risk: GeminiClient never run against the real SDK; the 1.20.0 pin and API calls are unverified
Risk: prompt not yet dry-run on 3 seeds (human step); README not updated for the LLM table
Failed approach: none
