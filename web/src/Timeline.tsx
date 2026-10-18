import type { RuntimeName } from "./data";
import { worldEffects, type TraceEvent } from "./trace";

export const ACTORS = ["agent", "runtime", "policy", "human", "world", "fault"] as const;

const WIDTH = 1000;
const LANE = 11;
const PAD = 6;
const HEIGHT = PAD * 2 + LANE * ACTORS.length;

export interface Lane {
  runtime: RuntimeName;
  events: TraceEvent[];
  hits: Set<number>;
  failed: boolean;
}

function laneY(actor: string): number {
  const index = ACTORS.indexOf(actor as (typeof ACTORS)[number]);
  return PAD + LANE * (index === -1 ? ACTORS.indexOf("runtime") : index) + LANE / 2;
}

function Mark({ event, x, hit }: { event: TraceEvent; x: number; hit: boolean }) {
  const y = laneY(event.actor);
  const label = `#${event.seq} ${event.event}`;
  let shape;
  if (event.event === "refund.issued") {
    shape = <circle className="actor-world effect" cx={x} cy={y} r={5} />;
  } else if (event.event === "label.created_record") {
    shape = <rect className="actor-world effect" x={x - 4.5} y={y - 4.5} width={9} height={9} />;
  } else if (event.actor === "fault") {
    shape = <path className="actor-fault effect" d={`M${x} ${y - 6}L${x + 6} ${y}L${x} ${y + 6}L${x - 6} ${y}Z`} />;
  } else {
    shape = <rect className={`actor-${event.actor}`} x={x - 1} y={y - 3.5} width={2} height={7} />;
  }
  return (
    <g>
      <title>{label}</title>
      {shape}
      {hit && <circle className="violation" cx={x} cy={y} r={8.5} />}
    </g>
  );
}

function describe(lane: Lane): string {
  const { refunds, labels } = worldEffects(lane.events);
  const faults = lane.events.filter((e) => e.actor === "fault").length;
  return `${lane.runtime}: ${lane.events.length} events, ${faults} faults, ${refunds} refunds, ${labels} labels`;
}

/** One strip per runtime, positioned by event index so the strips line up. */
export function Timeline({
  lanes,
  current,
  onSelect,
}: {
  lanes: Lane[];
  current: RuntimeName;
  onSelect: (runtime: RuntimeName) => void;
}) {
  const longest = Math.max(2, ...lanes.map((lane) => lane.events.length));
  const step = (WIDTH - PAD * 4) / (longest - 1);
  return (
    <div className="timeline">
      {lanes.map((lane) => {
        const { refunds, labels } = worldEffects(lane.events);
        return (
          <button
            type="button"
            key={lane.runtime}
            className="lane-row"
            aria-current={lane.runtime === current}
            onClick={() => onSelect(lane.runtime)}
          >
            <span className="lane-label">
              <span className={lane.failed ? "fail-text" : undefined}>{lane.runtime}</span>
              <small>
                {refunds} refund{refunds === 1 ? "" : "s"}, {labels} label{labels === 1 ? "" : "s"}
              </small>
            </span>
            <svg viewBox={`0 0 ${WIDTH} ${HEIGHT}`} role="img" aria-label={describe(lane)}>
              {ACTORS.map((actor) => (
                <line key={actor} className="guide" x1={0} x2={WIDTH} y1={laneY(actor)} y2={laneY(actor)} />
              ))}
              {lane.events.map((event, i) => (
                <Mark key={event.seq} event={event} x={PAD * 2 + i * step} hit={lane.hits.has(event.seq)} />
              ))}
            </svg>
          </button>
        );
      })}
    </div>
  );
}

export function TimelineLegend() {
  return (
    <ul className="legend" aria-label="Timeline legend">
      {ACTORS.filter((actor) => actor !== "fault").map((actor) => (
        <li key={actor}>
          <svg viewBox="0 0 12 12" aria-hidden="true">
            <rect className={`swatch-${actor}`} x={2} y={2} width={8} height={8} rx={1} />
          </svg>
          {actor}
        </li>
      ))}
      <li>
        <svg viewBox="0 0 12 12" aria-hidden="true">
          <circle className="swatch-world" cx={6} cy={6} r={5} />
        </svg>
        refund issued
      </li>
      <li>
        <svg viewBox="0 0 12 12" aria-hidden="true">
          <rect className="swatch-world" x={1.5} y={1.5} width={9} height={9} />
        </svg>
        label created
      </li>
      <li>
        <svg viewBox="0 0 12 12" aria-hidden="true">
          <path className="swatch-fault" d="M6 0L12 6L6 12L0 6Z" />
        </svg>
        injected fault
      </li>
      <li>
        <svg viewBox="0 0 12 12" aria-hidden="true">
          <circle className="swatch-violation" cx={6} cy={6} r={4.5} />
        </svg>
        behind a violation
      </li>
    </ul>
  );
}
