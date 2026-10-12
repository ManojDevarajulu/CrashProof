# REVIEW.md
Review only the latest task's diff. Report findings as a list. Do not fix anything
or edit any file.
1. Scope: any file, dependency, flag or feature outside AGENTS.md Sections 3 and 4
   or the layout in docs/ARCHITECTURE.md?
2. Determinism: any call banned by Rule 4, any dict or set iteration that affects
   output, any unseeded randomness?
3. Tests: any test weakened, skipped, deleted or vacuous? Does each new invariant
   have a negative test?
4. Baseline fairness: do the runtimes differ only in the layers each is defined to
   include (keys for keyed; keys, ledger, executor, reconcile, policy and audit log
   for hardened)?
5. Errors: any swallowed exception, `except: pass`, or None returned for failure?
6. Numbers: any hand-written result or percentage?
7. Logs: were STATE.md and LOG.md updated, and do they match the code and test output?
8. Verdict: PASS, or a list of blocking issues.
