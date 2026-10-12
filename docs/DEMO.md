# DEMO.md

Script for a screen recording, about 90 seconds spoken. Steps only.
Demo seed: 1 (carrier C, behavior normal). Naive issues two refunds after a
timeout; hardened issues one. All numbers below come from `make replay` output
and the two replay files.

## Before you record

- [ ] Regenerate the replays:
      `make replay SEED=1 RUNTIME=naive` and `make replay SEED=1 RUNTIME=hardened`
- [ ] Check the printed hashes match the ones in step 4. If not, stop and update this file.
- [ ] If opening viewer/index.html from file:// hides the hash, serve it:
      `python -m http.server 8000` in the repo root, then open
      `http://localhost:8000/viewer/index.html`

## Script

1. **Results table.** Open README.md and scroll to the generated results table.
   Say: "Naive runs each call the agent proposes; when the agent retries after a timeout, naive simply runs it again. Keyed is naive plus idempotency
   keys. Hardened adds a ledger, reconciliation and a policy engine. Same agent,
   same seeds, same simulated world; only the runtime differs."

2. **Naive, seed 1.** Open viewer/index.html and load
   `results/replay/seed-1-naive.jsonl`.
   - Point at seq 18, `fault.F2`, virtual time (vt) 6874: the refund was applied, then the call timed out.
   - Point at seq 16, `refund.issued` (rfd-000001), virtual time 6874.
   - Point at seq 21, `refund.issued` (rfd-000002), virtual time 6874.
   Say: "The agent retried after the timeout. Two refunds of 8842 against an order
   that paid 8842."

3. **Hardened, same seed.** Load `results/replay/seed-1-hardened.jsonl`.
   - Point at seq 30, `fault.F2`, virtual time (vt) 6874: the same fault.
   - Point at seq 31, `executor.unknown`: the effect is marked unknown, not retried.
   - Point at seq 35, `reconcile.applied`: the runtime asked the refund service by key and found it applied.
   - Point at seq 28, `refund.issued` (rfd-000001): the only refund.
   Say: "Same fault, same retry from the agent. One refund."

4. **Hashes.** With the hardened replay loaded, show the SHA-256 in the viewer:
   `32571347ac4fa35e59a9960eaeb97e3f05ce59bbf7184fe1f1eb4bdbe012595a`.
   Load the naive replay and show:
   `4a84a44c277b79fa0586272467cc065d0966e86285b75ad61c7595910f9b8f16`.
   Show the terminal output of `make replay SEED=1 RUNTIME=hardened`: run1 and run2
   print the same hash, because the command runs the seed twice and compares.
   Say: "The viewer hash equals the hash the command printed."

5. **Close.** Say: "This is a simulation with a scripted agent, not a measure of
   any real model. I most want to be upfront that the simulated providers encode my
   own assumptions about how real ones behave."
