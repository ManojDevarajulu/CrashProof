import { describe, expect, it } from "vitest";
import { DataError, parseIndex } from "./data";
import { counts, fixtureIndex } from "./fixture";

describe("parseIndex", () => {
  it("accepts a valid index", () => {
    const index = fixtureIndex();
    expect(parseIndex(structuredClone(index))).toEqual(index);
  });

  it.each([
    ["not an object", []],
    ["missing summary", { invariant_seeds: {}, seeds: [] }],
    ["count of the wrong type", { ...fixtureIndex(), summary: { seeds: 1, runtimes: { naive: { ...counts(), I1: "2" } } } }],
    ["seed missing a runtime", { ...fixtureIndex(), seeds: [{ seed: 1, scenario: {}, runtimes: {} }] }],
  ])("rejects %s", (_name, raw) => {
    expect(() => parseIndex(raw)).toThrow(DataError);
  });

  // Empty when `make site` has not run; CI runs it before these tests.
  const [exported] = Object.values(
    import.meta.glob("../public/data/index.json", { eager: true, import: "default" }),
  );
  it.skipIf(exported === undefined)("accepts the file make site wrote", () => {
    const index = parseIndex(exported);
    expect(index.seeds[0]?.seed).toBe(1);
    expect(index.summary.runtimes.hardened?.total_violating_seeds).toBe(0);
  });
});
