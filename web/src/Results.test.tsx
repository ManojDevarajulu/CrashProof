import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { InvariantMatrix, Results, RuntimeBars } from "./Results";
import { fixtureIndex } from "./fixture";

describe("Results", () => {
  const html = renderToStaticMarkup(<Results index={fixtureIndex()} />);

  it("shows each runtime's failed and passed seeds from the data", () => {
    expect(html).toContain('aria-label="naive: 2 of 10 seeds failed"');
    expect(html).toContain('<span class="fail-n">1</span> failed, <span class="pass-n">9</span> passed');
  });

  it("makes only non-zero counts selectable", () => {
    expect(html.match(/<button/g)).toHaveLength(3);
  });

  it("renders null counts as n/a", () => {
    expect(html).toContain('<td class="cell na">n/a</td>');
  });
});

describe("RuntimeBars", () => {
  it("orders runtimes naive, keyed, hardened", () => {
    const html = renderToStaticMarkup(<RuntimeBars seeds={10} runtimes={fixtureIndex().summary.runtimes} />);
    const order = [...html.matchAll(/<span class="name">(\w+)<\/span>/g)].map((m) => m[1]);
    expect(order).toEqual(["naive", "keyed", "hardened"]);
  });
});

describe("InvariantMatrix", () => {
  it("is read-only without a select handler", () => {
    const html = renderToStaticMarkup(<InvariantMatrix seeds={10} runtimes={fixtureIndex().summary.runtimes} />);
    expect(html).not.toContain("<button");
  });
});
