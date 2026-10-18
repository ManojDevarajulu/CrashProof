import { describe, expect, it } from "vitest";
import { parseRoute, seedHref } from "./route";
import {
  TraceError,
  formatVt,
  ledgerSteps,
  parseTrace,
  sha256Hex,
  violationSeqs,
  worldEffects,
  type TraceEvent,
} from "./trace";

function event(seq: number, name: string, data: Record<string, unknown> = {}): TraceEvent {
  return { seq, vt: 0, workflow_id: "wf-000001", actor: "world", event: name, data };
}

describe("parseRoute", () => {
  it.each([
    ["", { page: "results" }],
    ["#/", { page: "results" }],
    ["#/seed/12/keyed", { page: "seed", seed: 12, runtime: "keyed" }],
    ["#/seed/12/turbo", { page: "missing", hash: "#/seed/12/turbo" }],
    ["#/other", { page: "missing", hash: "#/other" }],
  ])("%s", (hash, route) => {
    expect(parseRoute(hash)).toEqual(route);
  });

  it("round-trips seedHref", () => {
    expect(parseRoute(seedHref(7, "hardened"))).toEqual({ page: "seed", seed: 7, runtime: "hardened" });
  });
});

describe("parseTrace", () => {
  it("reads canonical JSONL", () => {
    const text = '{"actor":"world","data":{},"event":"a","seq":0,"vt":0,"workflow_id":"w"}\n';
    expect(parseTrace(text)).toHaveLength(1);
  });

  it("rejects a missing final newline and non-events", () => {
    expect(() => parseTrace('{"seq":0}')).toThrow(TraceError);
    expect(() => parseTrace('{"nope":1}\n')).toThrow(TraceError);
  });
});

describe("formatVt", () => {
  it.each([
    [0, "0s"],
    [59, "59s"],
    [6874, "1h 54m 34s"],
    [86400 * 3 + 5, "3d 0h 0m 5s"],
  ])("%i", (vt, text) => {
    expect(formatVt(vt)).toBe(text);
  });
});

describe("violationSeqs", () => {
  const events = [
    event(1, "label.created_record"),
    event(2, "refund.issued", { refund_id: "rfd-000001" }),
    event(3, "refund.issued", { refund_id: "rfd-000002" }),
  ];

  it("points I3 and I4 at the named refund", () => {
    expect(violationSeqs({ invariant: "I3", detail: "refund rfd-000002: no_scan" }, events)).toEqual(new Set([3]));
  });

  it("points I1 at every refund and I5 at every label", () => {
    expect(violationSeqs({ invariant: "I1", detail: "wf-000001: 2 refunds" }, events)).toEqual(new Set([2, 3]));
    expect(violationSeqs({ invariant: "I5", detail: "wf-000001: 2 labels" }, events)).toEqual(new Set([1]));
  });

  it("has no single event for I7", () => {
    expect(violationSeqs({ invariant: "I7", detail: "wf-000001: not terminal" }, events).size).toBe(0);
  });
});

describe("ledger and world effects", () => {
  it("orders steps per effect and names the rule", () => {
    const steps = ledgerSteps([
      event(5, "executor.unknown", { effect_type: "issue_refund" }),
      event(6, "executor.denied", { effect_type: "create_label", rule_id: "P5" }),
      event(7, "reconcile.applied", { effect_type: "issue_refund" }),
      event(8, "refund.issued"),
    ]);
    expect(steps.get("issue_refund")).toEqual([
      { seq: 5, label: "UNKNOWN" },
      { seq: 7, label: "reconcile: applied → CONFIRMED" },
    ]);
    expect(steps.get("create_label")).toEqual([{ seq: 6, label: "denied P5" }]);
  });

  it("counts what the world recorded", () => {
    expect(worldEffects([event(1, "refund.issued"), event(2, "refund.issued")])).toEqual({ refunds: 2, labels: 0 });
  });
});

describe("sha256Hex", () => {
  it("matches a known digest", async () => {
    const bytes = new TextEncoder().encode("abc");
    expect(await sha256Hex(bytes.buffer)).toBe(
      "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
    );
  });
});

// Real seed 1 traces, present after `make site` (CI runs it first).
const replays = import.meta.glob("../public/data/replays/seed-1-*.jsonl", {
  eager: true,
  query: "?raw",
  import: "default",
}) as Record<string, string>;

describe.skipIf(Object.keys(replays).length === 0)("seed 1 traces", () => {
  const trace = (runtime: string) => parseTrace(replays[`../public/data/replays/seed-1-${runtime}.jsonl`] ?? "");

  it("naive refunds twice; hardened once, after reconciling the unknown refund", () => {
    expect(worldEffects(trace("naive")).refunds).toBe(2);
    const hardened = trace("hardened");
    expect(worldEffects(hardened).refunds).toBe(1);
    expect(ledgerSteps(hardened).get("issue_refund")?.map((s) => s.label)).toEqual([
      "UNKNOWN",
      "reconcile: applied → CONFIRMED",
    ]);
  });

  it("hashes to the value pinned in the README", async () => {
    const bytes = new TextEncoder().encode(replays["../public/data/replays/seed-1-hardened.jsonl"]);
    expect(await sha256Hex(bytes.buffer)).toBe(
      "32571347ac4fa35e59a9960eaeb97e3f05ce59bbf7184fe1f1eb4bdbe012595a",
    );
  });
});
