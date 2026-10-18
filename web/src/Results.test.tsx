import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { CountsTable, Results } from "./Results";
import { fixtureIndex } from "./fixture";

describe("Results", () => {
  const html = renderToStaticMarkup(<Results index={fixtureIndex()} />);

  it("states the headline from the data", () => {
    expect(html).toContain("Violating seeds out of 10: hardened 0 · keyed 1 · naive 2");
  });

  it("makes only non-zero counts selectable", () => {
    expect(html.match(/<button/g)).toHaveLength(3);
  });

  it("renders null counts as n/a", () => {
    expect(html).toContain('<td class="na">n/a</td>');
  });
});

describe("CountsTable", () => {
  it("is read-only without a select handler", () => {
    const html = renderToStaticMarkup(<CountsTable runtimes={fixtureIndex().summary.runtimes} />);
    expect(html).not.toContain("<button");
  });
});
