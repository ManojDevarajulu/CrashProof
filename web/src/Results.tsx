import { useState } from "react";
import {
  INVARIANTS,
  INVARIANT_LABELS,
  presentRuntimes,
  type Counts,
  type InvariantName,
  type RuntimeName,
  type SiteIndex,
} from "./data";

interface Selection {
  runtime: RuntimeName;
  invariant: InvariantName;
}

interface TableProps {
  runtimes: Partial<Record<RuntimeName, Counts>>;
  selected?: Selection | null;
  onSelect?: (selection: Selection) => void;
}

function CountCell({
  value,
  active,
  onClick,
}: {
  value: number | null;
  active: boolean;
  onClick?: () => void;
}) {
  if (value === null) return <td className="na">n/a</td>;
  if (value === 0 || !onClick) return <td className={value ? "bad" : "zero"}>{value}</td>;
  return (
    <td className="bad">
      <button type="button" aria-pressed={active} onClick={onClick}>
        {value}
      </button>
    </td>
  );
}

export function CountsTable({ runtimes, selected = null, onSelect }: TableProps) {
  return (
    <table>
      <thead>
        <tr>
          <th scope="col">runtime</th>
          {INVARIANTS.map((name) => (
            <th scope="col" key={name} title={INVARIANT_LABELS[name]}>
              {name}
            </th>
          ))}
          <th scope="col">violating seeds</th>
        </tr>
      </thead>
      <tbody>
        {presentRuntimes(runtimes).map((runtime) => {
          const counts = runtimes[runtime] as Counts;
          return (
            <tr key={runtime}>
              <th scope="row">{runtime}</th>
              {INVARIANTS.map((invariant) => (
                <CountCell
                  key={invariant}
                  value={counts[invariant]}
                  active={selected?.runtime === runtime && selected.invariant === invariant}
                  onClick={onSelect && (() => onSelect({ runtime, invariant }))}
                />
              ))}
              <td className={counts.total_violating_seeds ? "bad total" : "zero total"}>
                {counts.total_violating_seeds}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

function SeedList({ index, selection }: { index: SiteIndex; selection: Selection }) {
  const seeds = index.invariant_seeds[selection.runtime]?.[selection.invariant] ?? [];
  const exported = new Set(index.seeds.map((s) => s.seed));
  return (
    <section className="panel" aria-live="polite">
      <h2>
        {selection.runtime} · {selection.invariant} · {seeds.length} seeds
      </h2>
      <p className="muted">{INVARIANT_LABELS[selection.invariant]}</p>
      <p className="seeds">
        {seeds.map((seed) =>
          exported.has(seed) ? (
            <a key={seed} href={`#/seed/${seed}/${selection.runtime}`}>
              {seed}
            </a>
          ) : (
            <span key={seed}>{seed}</span>
          ),
        )}
      </p>
      <p className="muted">
        Linked seeds have a trace on this site. Any other seed reproduces locally with{" "}
        <code>make replay SEED=n RUNTIME={selection.runtime}</code>.
      </p>
    </section>
  );
}

export function Results({ index }: { index: SiteIndex }) {
  const [selected, setSelected] = useState<Selection | null>(null);
  const { summary } = index;
  const llm = summary.llm;
  return (
    <main>
      <p className="lede">
        Violating seeds out of {summary.seeds}:{" "}
        {presentRuntimes(summary.runtimes)
          .map((name) => `${name} ${summary.runtimes[name]?.total_violating_seeds}`)
          .join(" · ")}
      </p>
      <h2>Seeds violating each invariant (scripted agent)</h2>
      <CountsTable runtimes={summary.runtimes} selected={selected} onSelect={setSelected} />
      <p className="muted">
        Each cell counts seeds, not events. Select a count to list its seeds. Naive and keyed
        have no policy engine and no scan timeout, so their I3, I4 and I7 failures follow from
        missing components, not only from faults.
      </p>
      {selected && <SeedList index={index} selection={selected} />}
      {llm && (
        <>
          <h2>LLM agent ({llm.seeds.length} recorded seeds, separate from the table above)</h2>
          <CountsTable runtimes={llm.runtimes} />
          <p className="muted">
            Model: {llm.model}. One recorded run of one model on a small seed set; not a
            statistical estimate.
          </p>
        </>
      )}
    </main>
  );
}
