# INVARIANTS.md
Each invariant has a definition, a check, where it applies, and a negative test
that must prove the check fails when the invariant is violated.

- I1 At most one refund per return workflow.
  Check: count refunds per reference in the refund service. Applies: all runtimes.
- I2 Total refunded per order <= paid_amount.
  Check: refund service totals. Applies: all.
- I3 A refund is valid only if a delivered return.scanned event for its workflow
  precedes its refund by trace seq AND the oracle verdict is eligible. Check: trace order plus oracle. Applies: all.
- I4 A refund above refund_cap needs an approved ApprovalLog record for its
  workflow whose approval.decided event precedes the refund by trace seq.
  Check: refund amount vs the approvals log. Applies: all.
- I5 At most one return label per workflow.
  Check: carrier records by reference. Applies: all.
- I6 Every CONFIRMED effect has a matching policy_decisions row.
  Applies: hardened only (naive and keyed report "n/a").
- I7 After the main drain, the runner reconciles (hardened), advances virtual
  time 72 hours with a no-op event, drains again, and any workflow still
  non-terminal is stuck. Applies: all.
- I8 Every refund in the refund service maps to exactly one CONFIRMED ledger entry,
  and every CONFIRMED refund entry maps to exactly one refund.
  Applies: hardened only.
- I9 Running the same seed twice gives an identical trace_hash. Applies: all.

All nine invariants are implemented.
Negative test pattern: build a tiny state that deliberately violates the
invariant, assert the checker reports it, then assert a clean state passes.
