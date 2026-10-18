import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { RuntimeName } from "./data";
import { fixtureIndex } from "./fixture";
import { SeedView, type LoadedTrace } from "./Seed";
import type { TraceEvent } from "./trace";

const naiveEvents: TraceEvent[] = [
  { seq: 0, vt: 0, workflow_id: "wf-000001", actor: "world", event: "refund.issued", data: { refund_id: "rfd-000001" } },
  { seq: 1, vt: 0, workflow_id: "wf-000001", actor: "fault", event: "fault.F2", data: { attempt: 1 } },
  { seq: 2, vt: 60, workflow_id: "wf-000001", actor: "world", event: "refund.issued", data: { refund_id: "rfd-000002" } },
];
const hardenedEvents: TraceEvent[] = [
  { seq: 0, vt: 0, workflow_id: "wf-000001", actor: "world", event: "refund.issued", data: { refund_id: "rfd-000001" } },
  { seq: 1, vt: 0, workflow_id: "wf-000001", actor: "runtime", event: "executor.unknown", data: { effect_type: "issue_refund" } },
  { seq: 2, vt: 0, workflow_id: "wf-000001", actor: "runtime", event: "reconcile.applied", data: { effect_type: "issue_refund" } },
];

function render(runtime: RuntimeName, hash: LoadedTrace["hash"] = "verified") {
  const entry = fixtureIndex().seeds[0]!;
  const traces = {
    naive: { events: naiveEvents, hash },
    keyed: { events: hardenedEvents, hash },
    hardened: { events: hardenedEvents, hash },
  };
  return renderToStaticMarkup(<SeedView entry={entry} runtime={runtime} traces={traces} />);
}

describe("SeedView", () => {
  it("lists the violation and highlights the refunds behind it", () => {
    const html = render("naive");
    expect(html).toContain("At most one refund per return");
    expect(html).toContain("2 refunds");
    expect(html.match(/class="is-hit"/g)).toHaveLength(2);
    expect(html).toContain('class="is-fault"');
    expect(html).toContain("naive has no ledger");
  });

  it("draws one timeline strip per runtime with violation rings only where they apply", () => {
    const html = render("naive");
    expect(html.match(/class="lane-row"/g)).toHaveLength(3);
    expect(html.match(/class="violation"/g)).toHaveLength(2);
    expect(html).toContain('aria-label="naive: 3 events, 1 faults, 2 refunds, 0 labels"');
  });

  it("shows hardened's ledger steps with their tone", () => {
    const html = render("hardened");
    expect(html).toContain('<span class="step unsure" title="trace event #1">UNKNOWN #1</span>');
    expect(html).toContain("reconcile: applied → CONFIRMED #2");
    expect(html).toContain("All nine invariants hold for this run.");
  });

  it("reports whether the trace file matches its hash", () => {
    expect(render("hardened", "mismatch")).toContain("trace file does not match its hash");
    expect(render("hardened")).toContain("trace file matches its hash");
  });
});
