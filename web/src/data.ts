// Types and validation for web/public/data/index.json, written by `make site`.

export const RUNTIMES = ["naive", "keyed", "hardened"] as const;
export type RuntimeName = (typeof RUNTIMES)[number];

export const INVARIANTS = ["I1", "I2", "I3", "I4", "I5", "I6", "I7", "I8", "I9"] as const;
export type InvariantName = (typeof INVARIANTS)[number];

export const INVARIANT_LABELS: Record<InvariantName, string> = {
  I1: "At most one refund per return",
  I2: "Total refunded is at most the amount paid",
  I3: "Refund only after the return scan, and only if eligible",
  I4: "Refund above the cap only with human approval",
  I5: "At most one return label per return",
  I6: "Every confirmed effect has a policy decision (hardened only)",
  I7: "No workflow still open after activity stops",
  I8: "Refunds and ledger entries match one to one (hardened only)",
  I9: "The same seed gives the same trace hash",
};

export type Counts = Record<InvariantName, number | null> & {
  total_violating_seeds: number;
};

export interface Summary {
  seeds: number;
  runtimes: Partial<Record<RuntimeName, Counts>>;
  llm?: { model: string; seeds: number[]; runtimes: Partial<Record<RuntimeName, Counts>> };
}

export interface Violation {
  invariant: string;
  detail: string;
}

export interface SeedRun {
  outcome: string;
  restarts: number;
  trace_hash: string;
  violations: Violation[];
}

export interface SiteSeed {
  seed: number;
  scenario: Record<string, string | number | boolean>;
  runtimes: Record<RuntimeName, SeedRun>;
}

export interface SiteIndex {
  summary: Summary;
  invariant_seeds: Partial<Record<RuntimeName, Partial<Record<InvariantName, number[]>>>>;
  seeds: SiteSeed[];
}

export class DataError extends Error {}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function expect(condition: boolean, message: string): asserts condition {
  if (!condition) throw new DataError(message);
}

function checkCounts(where: string, counts: unknown): void {
  expect(isObject(counts), `${where}: counts must be an object`);
  for (const name of INVARIANTS) {
    const value = counts[name];
    expect(value === null || typeof value === "number", `${where}.${name}: not a count`);
  }
  expect(typeof counts.total_violating_seeds === "number", `${where}: no total`);
}

function checkRuntimes(where: string, runtimes: unknown): void {
  expect(isObject(runtimes), `${where}: runtimes must be an object`);
  for (const [name, counts] of Object.entries(runtimes)) {
    checkCounts(`${where}.${name}`, counts);
  }
}

function checkSeed(seed: unknown): void {
  expect(isObject(seed) && typeof seed.seed === "number", "seed entry without a seed");
  expect(isObject(seed.scenario), `seed ${seed.seed}: no scenario`);
  expect(isObject(seed.runtimes), `seed ${seed.seed}: no runtimes`);
  for (const name of RUNTIMES) {
    const run = seed.runtimes[name];
    expect(isObject(run), `seed ${seed.seed}: missing ${name}`);
    expect(typeof run.trace_hash === "string", `seed ${seed.seed} ${name}: no hash`);
    expect(Array.isArray(run.violations), `seed ${seed.seed} ${name}: no violations`);
  }
}

/** Validate the parsed index.json; throws DataError naming the first problem. */
export function parseIndex(raw: unknown): SiteIndex {
  expect(isObject(raw), "index.json must be an object");
  const { summary, invariant_seeds, seeds } = raw;
  expect(isObject(summary) && typeof summary.seeds === "number", "summary.seeds missing");
  checkRuntimes("summary", summary.runtimes);
  if (summary.llm !== undefined) {
    expect(isObject(summary.llm), "summary.llm must be an object");
    checkRuntimes("summary.llm", summary.llm.runtimes);
  }
  expect(isObject(invariant_seeds), "invariant_seeds missing");
  expect(Array.isArray(seeds), "seeds must be a list");
  seeds.forEach(checkSeed);
  return raw as unknown as SiteIndex;
}

/** Runtimes present in a runtimes map, in the report's fixed order. */
export function presentRuntimes(
  runtimes: Partial<Record<RuntimeName, Counts>>,
): RuntimeName[] {
  return (["hardened", "keyed", "naive"] as const).filter((name) => runtimes[name]);
}
