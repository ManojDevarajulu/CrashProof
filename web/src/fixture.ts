// A small, valid index.json for unit tests.
import type { Counts, SiteIndex } from "./data";

export function counts(overrides: Partial<Counts> = {}): Counts {
  return {
    I1: 0, I2: 0, I3: 0, I4: 0, I5: 0, I6: null, I7: 0, I8: null, I9: 0,
    total_violating_seeds: 0,
    ...overrides,
  };
}

export function fixtureIndex(): SiteIndex {
  const run = { outcome: "closed", restarts: 0, trace_hash: "ab".repeat(32), violations: [] };
  return {
    summary: {
      seeds: 10,
      runtimes: {
        hardened: counts({ I6: 0, I8: 0 }),
        keyed: counts({ I5: 1, total_violating_seeds: 1 }),
        naive: counts({ I1: 2, I2: 2, total_violating_seeds: 2 }),
      },
    },
    invariant_seeds: { naive: { I1: [1, 7], I2: [1, 7] }, keyed: { I5: [7] } },
    seeds: [
      {
        seed: 1,
        scenario: { behavior: "normal", carrier: "C" },
        runtimes: {
          naive: { ...run, violations: [{ invariant: "I1", detail: "2 refunds" }] },
          keyed: run,
          hardened: run,
        },
      },
    ],
  };
}
