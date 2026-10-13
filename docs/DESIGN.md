# Crashproof: design notes

A formatted version of these notes is in docs/design.html.

## The question

I wanted to know what happens when an AI agent has to do things with real side effects, and the systems it talks to fail halfway through. Returns and refunds are a good test. Every step creates something (a label, a refund), and every step can fail after the effect has already happened. The classic failure is a refund call that succeeds, a response that gets lost, and an agent that retries. The customer is paid twice.

## How a run works

Each seed is one order and one return, driven by a single-threaded scheduler on virtual time. There is no real clock, no threads and no network, so a run is fully determined by its seed and can be replayed exactly. The world (the store, three fictional carriers, a refund service and an event bus) survives simulated crashes. The runtime and the agent are rebuilt after one, the hardened runtime from its ledger. I built this with AI coding agents working under written rules, with a cross-model review step and the invariant checks described below as the safety net. I chose the design and read every result.

## Making the comparison fair

Every runtime sees the same faults for the same logical attempt. Each fault decision uses its own random stream, keyed by the seed, the fault type, the workflow, the kind of call and which attempt it is. Runtimes make different numbers of calls, so a shared random stream would give them different luck.

Four faults are injected: a timeout before the effect is applied, a timeout after it is applied (the lost response), a duplicated webhook, and a crash after the effect but before it is recorded. Four more were specified (out-of-order delivery, rate limits, provider error formats, long delays) and not built.

## Three runtimes

- naive: executes what the agent proposes. Retries only happen because the agent asks again. No keys, no ledger, no policy, no timeout.
- keyed: naive, plus a deterministic idempotency key sent to every provider that supports one.
- hardened: keys, a ledger, reconciliation, a policy engine with human escalation, and a scan timeout.

The keyed runtime exists because the obvious objection is "just use idempotency keys". It answers part of that.

## Inside the hardened runtime

The ledger records an intent before any provider call. If a call times out, the effect is marked unknown, and an unknown effect is never retried blindly. The runtime asks the provider whether it happened, by key or, for a provider that ignores keys, by reference, and acts on the answer.

Before each side effect a few policy rules run: for a label, the fraud flag, the delivery window and one label per workflow; for a refund, that the return was scanned, that the amount is within what was paid, and the refund cap. A refund above the cap goes to a simulated human approver. If no return scan arrives within 48 hours, the workflow ends as scan_timeout.

## What I measured

1,000 seeds, three runtimes, nine invariants. Seeds with at least one violation: hardened 0, keyed 631, naive 747.

Keyed had no duplicate refunds and no over-refunds. It did have 112 seeds with duplicate labels, because one carrier ignores keys, plus 286 seeds with a refund issued before the return scan or for an order that was not eligible, 176 that refunded above the cap with no approval, and 160 that never finished.

Naive and keyed have no policy engine and no scan timeout, so their policy and liveness failures follow from what they leave out, not only from faults. Counts are seeds, not events.

## How I checked the checkers

A zero is only worth something if the invariants can see a bug. I broke the hardened runtime five ways, ran 300 seeds each, and each one was caught:

- Refund key removed: duplicate refunds in 19 seeds, and ledger mismatches in 189 seeds.
- Key changed on every attempt: the same, plus duplicate labels in 15 seeds.
- Refund cap not enforced: 46 seeds with a refund above the cap and no approval.
- Fraud check disabled: 32 seeds refunded an order flagged for fraud.
- Scan timeout removed: 34 seeds left a workflow open.

A sixth change, skipping the final reconcile at the end of a run, changed nothing in any seed. I treat that code as a safety net the evaluation never needed. These runs were done on a throwaway branch, with an AI coding agent applying the patches. The patches are not in the repository.

## What this does not show

- It is a simulation. The carriers are fictional and their failures are my assumptions.
- The 1,000-seed results use a deterministic script, not a language model. A separate 3-seed recorded run of one model is a smoke test, not a measurement of any model.
- The fault rates and the scenario mix are arbitrary. Change them and the counts change.
- Hardened never left a workflow unfinished in these runs, but the scan timeout is what ensures that in these runs: removing it, 34 seeds left a workflow open. The end-of-run reconcile never changed an outcome in any measured seed.
- Policy rule P3 denied a step in 86 of the 1,000 hardened seeds, but those denials are not terminal: the workflow waits for the scan. P1 and P5 never denied in the measured seeds.
- For naive and keyed, the "never finished" check reads the runtime's own flag, because they have no ledger.
- The simulated refund service always supports idempotency keys and lookup by key; a refund provider without keys was not modelled.
- The 1,000 seeds are one sample of the scenario mix and the counts have no confidence interval.
- After a simulated crash, a rebuilt naive or keyed runtime has no memory of earlier effects or of webhooks it already received; this follows from their having no ledger.
- Crashes are injected only after an effect is applied, and execution is single-threaded; a crash between recording intent and calling the provider, and truly concurrent workers, are not modelled.

## Possible next steps

- Record more LLM seeds (up to the 50-seed cap). The LLMAgent and cassettes exist; only 3 seeds are recorded.
- Build the four skipped faults. Out-of-order delivery is the one I would expect to matter most.
- Run the same invariants against a provider sandbox, to see how wrong my assumptions are.

## Where to look in the code

- src/crashproof/clock.py: virtual clock and the discrete-event scheduler
- src/crashproof/world/faults.py: seeded fault injector and the simulated crash
- src/crashproof/runtime/executor.py: hardened side-effect flow and idempotency keys
- src/crashproof/runtime/ledger.py: the effects ledger and policy decision rows
- src/crashproof/runtime/reconcile.py: resolves unknown effects by asking the provider
- src/crashproof/runtime/policy.py: policy rules P1 to P6 and the approver escalation
- src/crashproof/sim/invariants.py: the nine invariant checks
- src/crashproof/sim/runner.py: runs one seed, handles crashes and quiescence

## Related work

This is not a new idea. Open-source tools and papers cover similar ground: simulation and fault-injection testing for tool-using agents (AgentProof, Deterministic Agent Simulator, afterstate, Eval Mock, Agent Reliability Lab), research on duplicate side effects in language-model agents under retries (the 'Where Does Exactly-Once Live?' paper and the verify-before-retry work), retry and idempotency middleware (latch-idempotent, Aura Guard), and the customer-service benchmark tau-bench, whose retail domain covers returns and refunds. The method itself, deterministic simulation testing, comes from FoundationDB and TigerBeetle. I have not compared this project against any of them. What it adds is a small, readable head-to-head of three runtime designs under identical injected faults, with a policy and escalation layer, checked by invariants that were themselves tested by breaking the system.
