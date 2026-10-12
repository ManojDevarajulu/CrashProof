# STATE.md
<!-- Overwrite this whole file at the end of every task. Max 25 lines. -->

Cut: 2 / 3
Last completed: C2-6 LLMAgent decisions and guards recorded
Next task: C2-6 implementation (LLMAgent, cassettes, make record)
Branch: main
Last verified: make test (668 passed), make lint clean, make readme clean
Demo seed 1: carrier C, behavior normal; hashes naive 4a84a44c..., hardened 32571347...
Gate 3: passed in clean WSL clone; repo pushed to https://github.com/ManojDevarajulu/CrashProof
Gate 2: 1000 seeds, hardened 0 for I1-I9
Last make eval (1000 seeds), violating seeds: hardened 0, keyed 631, naive 747
Frozen (do not touch): scenario_config.yaml, policy_rules.yaml
Makefile default RUNTIMES: naive,keyed,hardened; dev extras pinned with ==
Planned: C2-6 (LLMAgent with cassettes and gemini-2.0-flash)
Not built: C2-2 (F4 F6 F7 F8)
Scope change requests pending: none
Notes:
  - C2-6 guards added: prompt runtime-agnostic, frozen after 3-seed dry run, cost cap, secret scanning
  - Tests updated to match README without AI section
Risk: live Gemini recording requires GCP credentials and billing
Failed approach: none
