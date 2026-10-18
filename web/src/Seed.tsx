import { useEffect, useMemo, useState, type CSSProperties } from "react";
import {
  INVARIANT_LABELS,
  RUNTIMES,
  type InvariantName,
  type RuntimeName,
  type SiteIndex,
  type SiteSeed,
} from "./data";
import { seedHref } from "./route";
import { Timeline, TimelineLegend, type Lane } from "./Timeline";
import {
  formatVt,
  isLedgerEvent,
  ledgerSteps,
  parseTrace,
  sha256Hex,
  violationSeqs,
  type TraceEvent,
} from "./trace";

export type HashCheck = "verified" | "mismatch";

export interface LoadedTrace {
  events: TraceEvent[];
  hash: HashCheck;
}

const LONG_HEX = /^[0-9a-f]{64}$/;

function shorten(value: unknown): unknown {
  return typeof value === "string" && LONG_HEX.test(value) ? `${value.slice(0, 8)}…` : value;
}

function summarize(data: Record<string, unknown>): string {
  return Object.entries(data)
    .map(([key, value]) => {
      const shown = shorten(value);
      return `${key}=${typeof shown === "string" ? shown : JSON.stringify(shown)}`;
    })
    .join("  ");
}

function failedInvariants(entry: SiteSeed, runtime: RuntimeName): string[] {
  return [...new Set(entry.runtimes[runtime].violations.map((v) => v.invariant))];
}

function hitsFor(entry: SiteSeed, runtime: RuntimeName, events: TraceEvent[]): Set<number> {
  const seqs = new Set<number>();
  for (const violation of entry.runtimes[runtime].violations) {
    for (const seq of violationSeqs(violation, events)) seqs.add(seq);
  }
  return seqs;
}

export function Verdict({ entry, runtime }: { entry: SiteSeed; runtime: RuntimeName }) {
  const failed = failedInvariants(entry, runtime);
  return failed.length ? (
    <span className="pill fail">fail {failed.join(" ")}</span>
  ) : (
    <span className="pill pass">pass</span>
  );
}

function Scenario({ entry }: { entry: SiteSeed }) {
  const { carrier, behavior, variant, paid_amount } = entry.scenario;
  const facts: [string, unknown][] = [
    ["Carrier", carrier],
    ["Customer", behavior],
    ["Variant", variant ?? "none"],
    ["Paid", `${paid_amount} minor units`],
  ];
  return (
    <dl className="scenario">
      {facts.map(([name, value]) => (
        <div key={name}>
          <dt>{name}</dt>
          <dd>{String(value)}</dd>
        </div>
      ))}
    </dl>
  );
}

function Findings({ entry, runtime, events }: { entry: SiteSeed; runtime: RuntimeName; events: TraceEvent[] }) {
  const violations = entry.runtimes[runtime].violations;
  const steps = ledgerSteps(events);
  return (
    <div className="findings">
      <section className="panel">
        <div className="panel-head">
          <h3>Invariants</h3>
          <Verdict entry={entry} runtime={runtime} />
        </div>
        <div className="panel-body">
          {violations.length === 0 ? (
            <p className="muted">All nine invariants hold for this run.</p>
          ) : (
            <ul>
              {violations.map((v, i) => (
                <li key={i} className="finding">
                  <span className="pill fail">{v.invariant}</span>
                  <span>{INVARIANT_LABELS[v.invariant as InvariantName] ?? v.invariant}</span>
                  <span className="detail">{v.detail}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      </section>
      <section className="panel">
        <div className="panel-head">
          <h3>Effects ledger</h3>
        </div>
        <div className="panel-body">
          {runtime !== "hardened" ? (
            <p className="muted">
              {runtime} has no ledger, so nothing records which effects it started. A timeout leaves it
              guessing.
            </p>
          ) : (
            <dl className="ledger">
              {[...steps].map(([effect, list]) => (
                <div key={effect}>
                  <dt>{effect}</dt>
                  <dd>
                    {list.map((step, i) => (
                      <span key={step.seq} className="step-wrap">
                        {i > 0 && <span className="step-gap"> then </span>}
                        <span className={`step ${step.tone}`} title={`trace event #${step.seq}`}>
                          {step.label} #{step.seq}
                        </span>
                      </span>
                    ))}
                  </dd>
                </div>
              ))}
            </dl>
          )}
        </div>
      </section>
    </div>
  );
}

function EventLog({ events, hits }: { events: TraceEvent[]; hits: Set<number> }) {
  const [focus, setFocus] = useState(false);
  const rows = focus ? events.filter((e) => hits.has(e.seq) || e.actor === "fault" || isLedgerEvent(e)) : events;
  return (
    <section className="panel">
      <div className="panel-head">
        <h3>Event log</h3>
        <div className="log-tools">
          <span className="muted">
            {rows.length} of {events.length} events
          </span>
          <label>
            <input type="checkbox" checked={focus} onChange={(e) => setFocus(e.target.checked)} /> Only
            faults, ledger steps and violations
          </label>
        </div>
      </div>
      <ol className="log">
        {rows.map((e) => (
          <li
            key={e.seq}
            className={hits.has(e.seq) ? "is-hit" : e.actor === "fault" ? "is-fault" : undefined}
            style={{ "--dot": `var(--${e.actor})` } as CSSProperties}
          >
            <details>
              <summary>
                <span className="seq">#{e.seq}</span>
                <span className="vt">{formatVt(e.vt)}</span>
                <span className="actor">{e.actor}</span>
                <span>{e.event}</span>
                <span className="summary-data">{summarize(e.data)}</span>
              </summary>
              <pre>{JSON.stringify(e, null, 2)}</pre>
            </details>
          </li>
        ))}
      </ol>
    </section>
  );
}

interface SeedViewProps {
  entry: SiteSeed;
  runtime: RuntimeName;
  traces: Record<RuntimeName, LoadedTrace>;
}

export function SeedView({ entry, runtime, traces }: SeedViewProps) {
  const lanes: Lane[] = useMemo(
    () =>
      RUNTIMES.map((name) => ({
        runtime: name,
        events: traces[name].events,
        hits: hitsFor(entry, name, traces[name].events),
        failed: entry.runtimes[name].violations.length > 0,
      })),
    [entry, traces],
  );
  const current = lanes.find((lane) => lane.runtime === runtime) as Lane;
  const run = entry.runtimes[runtime];
  const hash = traces[runtime].hash;

  return (
    <>
      <section className="seed-head">
        <h1>Seed {entry.seed}</h1>
        <Scenario entry={entry} />
      </section>

      <section className="panel">
        <div className="panel-head">
          <h2>Same seed, three runtimes</h2>
          <span className="muted">Select a strip to inspect that run</span>
        </div>
        <div className="panel-body">
          <Timeline
            lanes={lanes}
            current={runtime}
            onSelect={(name) => (window.location.hash = seedHref(entry.seed, name))}
          />
          <TimelineLegend />
        </div>
      </section>

      <nav className="tabs" aria-label="Runtime">
        {RUNTIMES.map((name) => (
          <a key={name} href={seedHref(entry.seed, name)} aria-current={name === runtime ? "page" : undefined}>
            {name} <Verdict entry={entry} runtime={name} />
          </a>
        ))}
      </nav>

      <div className="run-facts">
        <span>
          Outcome <code>{run.outcome}</code>
        </span>
        <span>
          Restarts <code>{run.restarts}</code>
        </span>
        <span title={run.trace_hash}>
          SHA-256 <code>{run.trace_hash.slice(0, 12)}…</code>
        </span>
        <span role="status" className={hash === "verified" ? "pill pass" : "pill fail"}>
          {hash === "verified" ? "trace file matches its hash" : "trace file does not match its hash"}
        </span>
      </div>

      <Findings entry={entry} runtime={runtime} events={current.events} />
      <EventLog events={current.events} hits={current.hits} />
    </>
  );
}

type Load =
  | { state: "loading" }
  | { state: "error"; message: string }
  | { state: "ready"; traces: Record<RuntimeName, LoadedTrace> };

async function loadTrace(seed: number, runtime: RuntimeName, expected: string): Promise<LoadedTrace> {
  const response = await fetch(`data/replays/seed-${seed}-${runtime}.jsonl`);
  if (!response.ok) throw new Error(`${runtime} trace: HTTP ${response.status}`);
  const bytes = await response.arrayBuffer();
  const events = parseTrace(new TextDecoder().decode(bytes));
  return { events, hash: (await sha256Hex(bytes)) === expected ? "verified" : "mismatch" };
}

export function SeedPage({ index, seed, runtime }: { index: SiteIndex; seed: number; runtime: RuntimeName }) {
  const entry = index.seeds.find((s) => s.seed === seed);
  const [load, setLoad] = useState<Load>({ state: "loading" });

  useEffect(() => {
    if (!entry) return;
    let live = true;
    setLoad({ state: "loading" });
    Promise.all(RUNTIMES.map((name) => loadTrace(seed, name, entry.runtimes[name].trace_hash)))
      .then(([naive, keyed, hardened]) => {
        if (live) setLoad({ state: "ready", traces: { naive: naive!, keyed: keyed!, hardened: hardened! } });
      })
      .catch((error: unknown) => live && setLoad({ state: "error", message: String(error) }));
    return () => {
      live = false;
    };
  }, [entry, seed]);

  if (!entry) {
    return (
      <p role="alert" className="notice">
        Seed {seed} has no trace on this site. Reproduce it locally with{" "}
        <code>make replay SEED={seed} RUNTIME={runtime}</code>.
      </p>
    );
  }
  if (load.state === "loading") return <p className="muted">Loading traces for seed {seed}…</p>;
  if (load.state === "error") {
    return (
      <p role="alert" className="notice">
        Could not load the traces for seed {seed}: {load.message}
      </p>
    );
  }
  return <SeedView entry={entry} runtime={runtime} traces={load.traces} />;
}
