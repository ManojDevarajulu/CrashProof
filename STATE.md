# STATE.md
<!-- Overwrite this whole file at the end of every task. Max 25 lines. -->

Cut: Frontend F1-F6 / Real adapter R1-R5
Last completed: PR 5 (scope expansion merged to main: AGENTS.md, ARCHITECTURE.md, DECISIONS.md)
Next task: F1 (make site: export summary.json and ~20 seed replays to web/public/data/)
Branch: feat/f1-site-exporter
Last verified: pytest 692 passed, ruff clean, mypy strict clean, CI green on main
Last make eval (1000 seeds), violating seeds: hardened 0, keyed 631, naive 747
LLM eval (3 seeds), violating seeds: hardened 0, keyed 2, naive 3 (0 malformed outputs)
Gate 3: passed; repo pushed to https://github.com/ManojDevarajulu/CrashProof
Frozen (do not touch): scenario_config.yaml, policy_rules.yaml, LLM prompt (build_prompt)
Cassettes: tests/cassettes/ (seed-0001..0003.json + _record_summary.json); 0 secret markers
LLM table: integrated into README.md results section via make readme
google-genai==1.20.0 installed in .venv; GeminiClient supports GEMINI_API_KEY and Vertex AI
Model: gemini-2.5-flash-lite
Not built: C2-2 (F4 F6 F7 F8)
Scope change requests pending: none (F1-F6 and R1-R5 approved)
Risk: none
Note: live calls executed strictly via make record; tests and eval remain 100% offline
Failed approach: none
