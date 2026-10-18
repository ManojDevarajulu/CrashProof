import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { fixtureIndex } from "./fixture";
import { SeedView } from "./Seed";
import type { TraceEvent } from "./trace";

const events: TraceEvent[] = [
  { seq: 0, vt: 0, workflow_id: "wf-000001", actor: "world", event: "refund.issued", data: { refund_id: "rfd-000001" } },
  { seq: 1, vt: 0, workflow_id: "wf-000001", actor: "fault", event: "fault.F2", data: { attempt: 1 } },
  { seq: 2, vt: 60, workflow_id: "wf-000001", actor: "world", event: "refund.issued", data: { refund_id: "rfd-000002" } },
];

function render(runtime: "naive" | "hardened", hash: "verified" | "mismatch") {
  const index = fixtureIndex();
  const entry = index.seeds[0]!;
  return renderToStaticMarkup(
    <SeedView index={index} entry={entry} runtime={runtime} events={events} hash={hash} />,
  );
}

describe("SeedView", () => {
  it("lists the violation and highlights the refunds behind it", () => {
    const html = render("naive", "verified");
    expect(html).toContain("I1 At most one refund per return: 2 refunds");
    expect(html.match(/class="hit"/g)).toHaveLength(2);
    expect(html).toContain('class="fault"');
    expect(html).toContain("World recorded 2 refunds and 0 labels.");
    expect(html).toContain("No ledger: naive");
  });

  it("marks the current runtime tab and the other tabs' verdicts", () => {
    const html = render("naive", "verified");
    expect(html).toContain('aria-current="page" class="bad">naive ✖ I1</a>');
    expect(html).toContain('class="ok">hardened ✓</a>');
  });

  it("reports a hash mismatch", () => {
    expect(render("hardened", "mismatch")).toContain("✖ file does not match");
    expect(render("hardened", "verified")).toContain("✓ matches the file");
  });
});
