import { RUNTIMES, type RuntimeName, type SiteIndex, type SiteSeed } from "./data";
import { seedHref, type Route } from "./route";

const INITIAL: Record<RuntimeName, string> = { naive: "n", keyed: "k", hardened: "h" };

function Marks({ entry }: { entry: SiteSeed }) {
  return (
    <span className="marks">
      {RUNTIMES.map((name) => {
        const failed = entry.runtimes[name].violations.length > 0;
        return (
          <span
            key={name}
            className={`mark ${failed ? "fail" : "pass"}`}
            title={`${name} ${failed ? "fails" : "passes"}`}
            aria-label={`${name} ${failed ? "fails" : "passes"}`}
          >
            {INITIAL[name]}
          </span>
        );
      })}
    </span>
  );
}

function describe(entry: SiteSeed): string {
  const { carrier, behavior } = entry.scenario;
  return `carrier ${String(carrier)}, ${String(behavior).replaceAll("_", " ")}`;
}

export function Sidebar({ index, route }: { index: SiteIndex; route: Route }) {
  const runtime = route.page === "seed" ? route.runtime : "naive";
  return (
    <nav className="sidebar" aria-label="Runs">
      <a className="overview-link" href="#/" aria-current={route.page === "results" ? "page" : undefined}>
        Overview
      </a>
      <h2>Traces</h2>
      <p className="muted">{index.seeds.length} seeds with full traces. Marks show naive, keyed and hardened.</p>
      <ul className="seed-list">
        {index.seeds.map((entry) => (
          <li key={entry.seed}>
            <a
              href={seedHref(entry.seed, runtime)}
              aria-current={route.page === "seed" && route.seed === entry.seed ? "page" : undefined}
            >
              <span className="mono">Seed {entry.seed}</span>
              <Marks entry={entry} />
              <span className="what">{describe(entry)}</span>
            </a>
          </li>
        ))}
      </ul>
    </nav>
  );
}

/** Below the sidebar breakpoint, a select replaces the list. */
export function MobileSeedPicker({ index, route }: { index: SiteIndex; route: Route }) {
  const runtime = route.page === "seed" ? route.runtime : "naive";
  return (
    <label className="mobile-seed">
      Trace{" "}
      <select
        value={route.page === "seed" ? route.seed : ""}
        onChange={(e) => (window.location.hash = e.target.value ? seedHref(Number(e.target.value), runtime) : "#/")}
      >
        <option value="">Overview</option>
        {index.seeds.map((entry) => (
          <option key={entry.seed} value={entry.seed}>
            Seed {entry.seed}: {describe(entry)}
          </option>
        ))}
      </select>
    </label>
  );
}
