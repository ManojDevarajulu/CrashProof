import { useState, type CSSProperties } from "react";
import {
  INVARIANTS,
  INVARIANT_LABELS,
  type Counts,
  type InvariantName,
  type RuntimeName,
  type SiteIndex,
} from "./data";
import { seedHref } from "./route";

const STORY_ORDER = ["naive", "keyed", "hardened"] as const;

interface Selection {
  runtime: RuntimeName;
  invariant: InvariantName;
}

function present(runtimes: Partial<Record<RuntimeName, Counts>>): RuntimeName[] {
  return STORY_ORDER.filter((name) => runtimes[name]);
}

export function RuntimeBars({ seeds, runtimes }: { seeds: number; runtimes: Partial<Record<RuntimeName, Counts>> }) {
  return (
    <div className="bars">
      {present(runtimes).map((name) => {
        const failed = runtimes[name]!.total_violating_seeds;
        const passed = seeds - failed;
        return (
          <div className="bar-row" key={name}>
            <span className="name">{name}</span>
            <div className="bar" role="img" aria-label={`${name}: ${failed} of ${seeds} seeds failed`}>
              <span className="failed" style={{ width: `${(failed / seeds) * 100}%` }} />
              <span className="passed" style={{ width: `${(passed / seeds) * 100}%` }} />
            </div>
            <span className="tally">
              <span className="fail-n">{failed}</span> failed, <span className="pass-n">{passed}</span> passed
            </span>
          </div>
        );
      })}
    </div>
  );
}

interface MatrixProps {
  seeds: number;
  runtimes: Partial<Record<RuntimeName, Counts>>;
  selected?: Selection | null;
  onSelect?: (selection: Selection) => void;
}

function MatrixCell({
  value,
  seeds,
  active,
  onClick,
  label,
}: {
  value: number | null;
  seeds: number;
  active: boolean;
  onClick?: () => void;
  label: string;
}) {
  if (value === null) return <td className="cell na">n/a</td>;
  if (value === 0) return <td className="cell zero">0</td>;
  const heat = Math.min(1, 0.25 + (value / seeds) * 2);
  const style = { "--heat": heat } as CSSProperties;
  const className = `cell hit${heat > 0.55 ? " hot" : ""}`;
  if (!onClick) {
    return (
      <td className={className} style={style}>
        {value}
      </td>
    );
  }
  return (
    <td className={className} style={style}>
      <button type="button" aria-pressed={active} aria-label={label} onClick={onClick}>
        {value}
      </button>
    </td>
  );
}

export function InvariantMatrix({ seeds, runtimes, selected = null, onSelect }: MatrixProps) {
  const names = present(runtimes);
  return (
    <table className="matrix">
      <thead>
        <tr>
          <th scope="col">Invariant</th>
          {names.map((name) => (
            <th scope="col" key={name}>
              {name}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {INVARIANTS.map((invariant) => (
          <tr key={invariant}>
            <th scope="row">
              <span className="inv">{invariant}</span>
              <span className="label">{INVARIANT_LABELS[invariant]}</span>
            </th>
            {names.map((runtime) => (
              <MatrixCell
                key={runtime}
                value={runtimes[runtime]![invariant]}
                seeds={seeds}
                active={selected?.runtime === runtime && selected.invariant === invariant}
                label={`${runtime} ${invariant}: ${runtimes[runtime]![invariant]} seeds, list them`}
                onClick={onSelect && (() => onSelect({ runtime, invariant }))}
              />
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function SeedPanel({ index, selection }: { index: SiteIndex; selection: Selection }) {
  const seeds = index.invariant_seeds[selection.runtime]?.[selection.invariant] ?? [];
  const exported = new Set(index.seeds.map((s) => s.seed));
  return (
    <section className="panel" aria-live="polite">
      <div className="panel-head">
        <h3>
          {seeds.length} seeds where {selection.runtime} fails {selection.invariant}
        </h3>
        <span className="muted">{INVARIANT_LABELS[selection.invariant]}</span>
      </div>
      <div className="panel-body">
        <p className="seeds">
          {seeds.map((seed) =>
            exported.has(seed) ? (
              <a key={seed} href={seedHref(seed, selection.runtime)}>
                {seed}
              </a>
            ) : (
              <span key={seed}>{seed}</span>
            ),
          )}
        </p>
        <p className="note">
          Highlighted seeds have a trace on this site. Every other seed replays locally with{" "}
          <code>make replay SEED=n RUNTIME={selection.runtime}</code>.
        </p>
      </div>
    </section>
  );
}

export function Results({ index }: { index: SiteIndex }) {
  const [selected, setSelected] = useState<Selection | null>(null);
  const { summary } = index;
  const llm = summary.llm;
  return (
    <>
      <section className="panel">
        <div className="panel-head">
          <h1>Seeds that broke at least one invariant</h1>
          <span className="muted">{summary.seeds} seeds, same agent, faults and world for every runtime</span>
        </div>
        <div className="panel-body">
          <RuntimeBars seeds={summary.seeds} runtimes={summary.runtimes} />
        </div>
      </section>

      <section className="panel">
        <div className="panel-head">
          <h2>Failures by invariant</h2>
          <span className="muted">Seeds per cell. Select a count to list its seeds.</span>
        </div>
        <div className="panel-body">
          <InvariantMatrix seeds={summary.seeds} runtimes={summary.runtimes} selected={selected} onSelect={setSelected} />
          <p className="note">
            Naive and keyed have no policy engine and no scan timeout, so their I3, I4 and I7 failures follow from
            missing components, not only from faults. I6 and I8 read the ledger, which only hardened has.
          </p>
        </div>
      </section>

      {selected && <SeedPanel index={index} selection={selected} />}

      {llm && (
        <section className="panel">
          <div className="panel-head">
            <h2>LLM agent, recorded</h2>
            <span className="muted">
              {llm.seeds.length} seeds with {llm.model}, kept apart from the scripted results
            </span>
          </div>
          <div className="panel-body">
            <InvariantMatrix seeds={llm.seeds.length} runtimes={llm.runtimes} />
            <p className="note">One recorded run of one model on a small seed set; not a statistical estimate.</p>
          </div>
        </section>
      )}
    </>
  );
}
