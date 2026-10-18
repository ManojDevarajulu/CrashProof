# STATE.md
<!-- Overwrite this whole file at the end of every task. Max 25 lines. -->

Cut: 2 / 3
Last completed: PR 2: compact README report (build_readme_report wired into make readme), keys bullets tightened
Next task: awaiting human (PR 2 review/merge)
Branch: feat/compact-readme-report
Last verified: make test (692 passed), make lint clean, make readme clean
Last make eval (1000 seeds), violating seeds: hardened 0, keyed 631, naive 747
LLM eval (3 seeds), violating seeds: hardened 0, keyed 2, naive 3 (0 malformed outputs)
Gate 3: passed in clean WSL clone; repo pushed to https://github.com/ManojDevarajulu/CrashProof
Frozen (do not touch): scenario_config.yaml, policy_rules.yaml, LLM prompt (build_prompt)
Cassettes: tests/cassettes/ (seed-0001..0003.json + _record_summary.json); 0 secret markers
LLM table: integrated into README.md results section via make readme
google-genai==1.20.0 installed in .venv; GeminiClient supports GEMINI_API_KEY and Vertex AI
Model: gemini-2.5-flash-lite
Not built: C2-2 (F4 F6 F7 F8)
Scope change requests pending: none
Risk: none
Note: live calls executed strictly via make record; tests and eval remain 100% offline
Failed approach: none
