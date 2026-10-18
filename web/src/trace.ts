// Parsing and reading of the canonical JSONL traces written by `make site`.
import type { Violation } from "./data";

export interface TraceEvent {
  seq: number;
  vt: number;
  workflow_id: string;
  actor: string;
  event: string;
  data: Record<string, unknown>;
}

export class TraceError extends Error {}

export function parseTrace(text: string): TraceEvent[] {
  if (!text.endsWith("\n")) throw new TraceError("trace must end with a newline");
  return text
    .slice(0, -1)
    .split("\n")
    .map((line, index) => {
      const event: unknown = JSON.parse(line);
      if (
        typeof event !== "object" ||
        event === null ||
        typeof (event as TraceEvent).seq !== "number" ||
        typeof (event as TraceEvent).event !== "string"
      ) {
        throw new TraceError(`line ${index + 1} is not a trace event`);
      }
      return event as TraceEvent;
    });
}

/** SHA-256 hex of the exact file bytes, as `make replay` prints it. */
export async function sha256Hex(bytes: ArrayBuffer): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, "0")).join("");
}

/** Virtual seconds as "1d 2h 3m 4s", dropping leading zero units. */
export function formatVt(vt: number): string {
  const parts: [number, string][] = [
    [Math.floor(vt / 86400), "d"],
    [Math.floor(vt / 3600) % 24, "h"],
    [Math.floor(vt / 60) % 60, "m"],
    [vt % 60, "s"],
  ];
  const first = parts.findIndex(([value]) => value > 0);
  if (first === -1) return "0s";
  return parts
    .slice(first)
    .map(([value, unit]) => `${value}${unit}`)
    .join(" ");
}

/** Seqs of the world records a violation is about. Details come from
 * sim/invariants.py: "refund rfd-N: ..." names one refund; I1 and I2 are about
 * all refunds; I5 about all labels; I7 has no single event. */
export function violationSeqs(violation: Violation, events: TraceEvent[]): Set<number> {
  const refundId = /\brfd-\d+\b/.exec(violation.detail)?.[0];
  const matches = (e: TraceEvent): boolean => {
    if (refundId) return e.event === "refund.issued" && e.data.refund_id === refundId;
    if (violation.invariant === "I1" || violation.invariant === "I2") {
      return e.event === "refund.issued";
    }
    if (violation.invariant === "I5") return e.event === "label.created_record";
    return false;
  };
  return new Set(events.filter(matches).map((e) => e.seq));
}

export type StepTone = "good" | "unsure" | "bad" | "plain";

const LEDGER_STEPS: Record<string, [string, StepTone]> = {
  "executor.confirmed": ["CONFIRMED", "good"],
  "executor.unknown": ["UNKNOWN", "unsure"],
  "executor.unknown_pending": ["UNKNOWN (no reconciler)", "unsure"],
  "executor.failed": ["FAILED", "bad"],
  "executor.dedup": ["dedup: already CONFIRMED", "plain"],
  "executor.denied": ["denied", "bad"],
  "executor.escalated": ["escalated", "plain"],
  "executor.exhausted": ["attempts exhausted", "bad"],
  "reconcile.applied": ["reconcile: applied → CONFIRMED", "good"],
  "reconcile.not_applied": ["reconcile: not applied → FAILED", "bad"],
};

export function isLedgerEvent(e: TraceEvent): boolean {
  return e.event in LEDGER_STEPS;
}

export interface LedgerStep {
  seq: number;
  label: string;
  tone: StepTone;
}

/** Ledger history per effect type, from the executor and reconcile events. */
export function ledgerSteps(events: TraceEvent[]): Map<string, LedgerStep[]> {
  const steps = new Map<string, LedgerStep[]>();
  for (const e of events) {
    const known = LEDGER_STEPS[e.event];
    const effect = e.data.effect_type;
    if (known === undefined || typeof effect !== "string") continue;
    const rule = typeof e.data.rule_id === "string" ? ` ${e.data.rule_id}` : "";
    const list = steps.get(effect) ?? [];
    list.push({ seq: e.seq, label: known[0] + rule, tone: known[1] });
    steps.set(effect, list);
  }
  return steps;
}

/** Side effects the world recorded, whatever the runtime believed. */
export function worldEffects(events: TraceEvent[]): { refunds: number; labels: number } {
  return {
    refunds: events.filter((e) => e.event === "refund.issued").length,
    labels: events.filter((e) => e.event === "label.created_record").length,
  };
}
