import { useEffect, useMemo, useState } from "react";
import { INVARIANT_LABELS, RUNTIMES, type InvariantName, type RuntimeName, type SiteIndex, type SiteSeed } from "./data";
import { seedHref } from "./route";
import {
  formatVt,
  isLedgerEvent,
  ledgerSteps,
  parseTrace,
  sha256Hex,
  violationSeqs,
  worldEffects,
  type TraceEvent,
} from "./trace";

export type HashCheck = "checking" | "verified" | "mismatch";

const LONG_HEX = /^[0-9a-f]{64}$/;

function formatData(data: Record<string, unknown>): string {
  return Object.entries(data)
    .map(([key, value]) => {
      const shown = typeof value === "string" && LONG_HEX.test(value) ? `${value.slice(0, 8)}…` : value;
      return `${key}=${typeof shown === "string" ? shown : JSON.stringify(shown)}`;
    })
    .join(" ");
}

function verdict(entry: SiteSeed, runtime: RuntimeName): string {
  const names = [...new Set(entry.runtimes[runtime].violations.map((v) => v.invariant))];
  return names.length ? `✖ ${names.join(" ")}` : "✓";
}

export function RuntimeTabs({ entry, current }: { entry: SiteSeed; current?: RuntimeName }) {
  return (
    <nav className="tabs" aria-label="Runtime">
      {RUNTIMES.map((runtime) => (
        <a
          key={runtime}
          href={seedHref(entry.seed, runtime)}
          aria-current={runtime === current ? "page" : undefined}
          className={entry.runtimes[runtime].violations.length ? "bad" : "ok"}
        >
          {runtime} {verdict(entry, runtime)}
        </a>
      ))}
    </nav>
  );
}

function Ledger({ runtime, events }: { runtime: RuntimeName; events: TraceEvent[] }) {
  const effects = worldEffects(events);
  const steps = ledgerSteps(events);
  return (
    <section>
      <h3>Side effects</h3>
      <p>
        World recorded {effects.refunds} refund{effects.refunds === 1 ? "" : "s"} and{" "}
        {effects.labels} label{effects.labels === 1 ? "" : "s"}.
      </p>
      {runtime === "hardened" ? (
        <dl className="ledger">
          {[...steps].map(([effect, list]) => (
            <div key={effect}>
              <dt>{effect}</dt>
              <dd>{list.map((step) => `${step.label} (#${step.seq})`).join("  →  ")}</dd>
            </div>
          ))}
        </dl>
      ) : (
        <p className="muted">No ledger: {runtime} keeps no record of the effects it started.</p>
      )}
    </section>
  );
}

interface SeedViewProps {
  index: SiteIndex;
  entry: SiteSeed;
  runtime: RuntimeName;
  events: TraceEvent[];
  hash: HashCheck;
}

export function SeedView({ index, entry, runtime, events, hash }: SeedViewProps) {
  const [focus, setFocus] = useState(false);
  const run = entry.runtimes[runtime];
  const hits = useMemo(() => {
    const seqs = new Set<number>();
    for (const violation of run.violations) {
      for (const seq of violationSeqs(violation, events)) seqs.add(seq);
    }
    return seqs;
  }, [run, events]);
  const { carrier, behavior, variant, paid_amount } = entry.scenario;
  const rows = focus
    ? events.filter((e) => hits.has(e.seq) || e.actor === "fault" || isLedgerEvent(e))
    : events;

  return (
    <main>
      <p>
        <a href="#/">← results</a>{" "}
        <select
          aria-label="Seed"
          value={entry.seed}
          onChange={(e) => (window.location.hash = seedHref(Number(e.target.value), runtime))}
        >
          {index.seeds.map((s) => (
            <option key={s.seed} value={s.seed}>
              seed {s.seed}
            </option>
          ))}
        </select>
      </p>
      <h2>
        Seed {entry.seed} · carrier {String(carrier)} · {String(behavior)}
        {variant && variant !== "none" ? ` · ${String(variant)}` : ""} · paid {String(paid_amount)}
      </h2>
      <RuntimeTabs entry={entry} current={runtime} />
      <p className="muted">
        outcome {run.outcome} · restarts {run.restarts} · trace sha256{" "}
        <code title={run.trace_hash}>{run.trace_hash.slice(0, 16)}…</code>{" "}
        <span className={hash === "mismatch" ? "bad" : hash === "verified" ? "ok" : ""} role="status">
          {hash === "verified" ? "✓ matches the file" : hash === "mismatch" ? "✖ file does not match" : "checking…"}
        </span>
      </p>
      <section>
        <h3>Invariants</h3>
        {run.violations.length === 0 ? (
          <p className="ok">No invariant violated.</p>
        ) : (
          <ul>
            {run.violations.map((v, i) => (
              <li key={i} className="bad">
                {v.invariant} {INVARIANT_LABELS[v.invariant as InvariantName] ?? ""}: {v.detail}
              </li>
            ))}
          </ul>
        )}
      </section>
      <Ledger runtime={runtime} events={events} />
      <h3>Trace ({events.length} events)</h3>
      <label className="muted">
        <input type="checkbox" checked={focus} onChange={(e) => setFocus(e.target.checked)} /> only
        faults, ledger events and rows behind a violation
      </label>
      <table className="trace">
        <thead>
          <tr>
            <th scope="col">seq</th>
            <th scope="col">vt</th>
            <th scope="col">actor</th>
            <th scope="col">event</th>
            <th scope="col">data</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((e) => (
            <tr key={e.seq} className={hits.has(e.seq) ? "hit" : e.actor === "fault" ? "fault" : undefined}>
              <td>{e.seq}</td>
              <td>{formatVt(e.vt)}</td>
              <td>{e.actor}</td>
              <td>{e.event}</td>
              <td className="data">{formatData(e.data)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </main>
  );
}

type Load =
  | { state: "loading" }
  | { state: "error"; message: string }
  | { state: "ready"; events: TraceEvent[]; hash: HashCheck };

export function SeedPage({ index, seed, runtime }: { index: SiteIndex; seed: number; runtime: RuntimeName }) {
  const entry = index.seeds.find((s) => s.seed === seed);
  const [load, setLoad] = useState<Load>({ state: "loading" });

  useEffect(() => {
    if (!entry) return;
    let live = true;
    setLoad({ state: "loading" });
    fetch(`data/replays/seed-${seed}-${runtime}.jsonl`)
      .then((response) => {
        if (!response.ok) throw new Error(`trace: HTTP ${response.status}`);
        return response.arrayBuffer();
      })
      .then(async (bytes) => {
        const events = parseTrace(new TextDecoder().decode(bytes));
        const hash = (await sha256Hex(bytes)) === entry.runtimes[runtime].trace_hash;
        if (live) setLoad({ state: "ready", events, hash: hash ? "verified" : "mismatch" });
      })
      .catch((error: unknown) => live && setLoad({ state: "error", message: String(error) }));
    return () => {
      live = false;
    };
  }, [entry, seed, runtime]);

  if (!entry) {
    return (
      <main>
        <p role="alert">
          Seed {seed} has no trace on this site. Reproduce it with{" "}
          <code>make replay SEED={seed} RUNTIME={runtime}</code>. <a href="#/">← results</a>
        </p>
      </main>
    );
  }
  if (load.state === "loading") return <p className="muted">Loading trace…</p>;
  if (load.state === "error") return <p role="alert" className="error">Could not load the trace: {load.message}</p>;
  return <SeedView index={index} entry={entry} runtime={runtime} events={load.events} hash={load.hash} />;
}
