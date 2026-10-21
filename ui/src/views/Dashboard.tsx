// The Live dashboard (D31): machine load, launch events and one lane per sensor / layer on one
// time axis. The pointer marks the same moment in every chart; every topic opens in the graph
// (deep dive); the one-level answers are in the right panel ("What changed").
import { useState } from "react";
import { go } from "../actions";
import { clock, fmtHz, indexAt, niceMax, rangeLabel, sparkPath, valueAt, xPct } from "../spark";
import { useStore } from "../store";
import type { Change, Lane, LaneTopic, Timeline } from "../types";
import { Pill } from "../ui";

const RANGES = [60, 180, 360, 720]; // probe windows
const SHOWN = 6; // topics per lane before "show more"
const STATUS: Record<string, [string, string]> = { // icon + tone: state is never colour alone
  healthy: ["✓", "ok"], degraded: ["▲", "warn"], unhealthy: ["✕", "bad"], idle: ["–", ""], "": ["·", ""],
};

export const selectChange = (id: string) =>
  useStore.getState().set({ selected: id, details: { view: "changes", id: "" } });

const windowSeconds = (tl: Timeline) => {
  const w = tl.windows;
  if (w.length < 2) return 5;
  const span = (Date.parse(w[w.length - 1].t) - Date.parse(w[0].t)) / 1000;
  return span > 0 ? span / (w.length - 1) / (tl.bucket ?? 1) : 5;
};

export function Dashboard() {
  const tl = useStore((s) => s.timeline);
  const hasLive = useStore((s) => !!s.meta?.has_live);
  if (!hasLive) {
    return <Empty title="No live data" lines={["Serve with the probe's dump: adviewer up --online",
      "(or adviewer serve graph.json --live probe_dump.json)."]} />;
  }
  if (!tl) return <Empty title="Loading the live history…" lines={[]} />;
  if (!tl.available) {
    return <Empty title="No history yet" lines={[
      `adviewer probe run keeps every probe window in ${tl.history ?? "the history file"} automatically.`,
      "Started the probe another way? Run adviewer record next to it.",
      "The dashboard fills in after the first few windows."]} />;
  }
  return (
    <div className="dash">
      <Header tl={tl} />
      <Machine tl={tl} />
      <Events tl={tl} />
      {tl.lanes.map((lane) => <LaneCard key={lane.name} lane={lane} n={tl.windows.length} />)}
    </div>
  );
}

function Empty({ title, lines }: { title: string; lines: string[] }) {
  return (
    <div className="empty">
      <div className="big">{title}</div>
      {lines.map((l) => <div key={l}>{l}</div>)}
    </div>
  );
}

function useSelected(): Change | undefined {
  return useStore((s) => s.timeline?.changes.find((c) => c.id === s.selected));
}

function Header({ tl }: { tl: Timeline }) {
  const range = useStore((s) => s.range);
  const hover = useStore((s) => s.hoverIdx);
  const ws = windowSeconds(tl);
  const sm = tl.summary;
  return (
    <div className="dash-head">
      <div>
        <h2>Live dashboard</h2>
        <div className="sub">
          {clock(sm.from ?? "")} – {clock(sm.to ?? "")} · {sm.nodes} nodes · {sm.topics} topics ·{" "}
          {sm.changes} change(s) with an effect
          {(tl.bucket ?? 1) > 1 && <> · 1 point = {tl.bucket} windows (lowest rate kept)</>}
          {hover !== null && tl.windows[hover] && <> · pointer <b>{clock(tl.windows[hover].t)}</b></>}
        </div>
      </div>
      <div className="seg" role="group" aria-label="time range">
        {RANGES.map((r) => (
          <button key={r} className={range === r ? "on" : ""} onClick={() => useStore.getState().set({ range: r })}>
            {rangeLabel(r, ws)}
          </button>
        ))}
      </div>
    </div>
  );
}

/** One chart on the shared time axis: lines, a dashed threshold, event markers, the pointer. */
function Chart({ series, max, n, label, warn, tall }: {
  series: { values: (number | null)[]; cls?: string }[]; max: number; n: number; label: string;
  warn?: number | null; tall?: boolean;
}) {
  const hover = useStore((s) => s.hoverIdx);
  const events = useStore((s) => s.timeline?.events ?? []);
  const sel = useSelected();
  const h = tall ? 56 : 28;
  const w = Math.max(n - 1, 1);
  const y = (v: number) => h - (Math.min(v, max) / max) * h;
  const move = (e: React.MouseEvent<SVGSVGElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    const i = indexAt(e.clientX - r.left, r.width, n);
    if (i !== useStore.getState().hoverIdx) useStore.getState().set({ hoverIdx: i });
  };
  return (
    <svg className={`chart${tall ? " tall" : ""}`} viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none"
      role="img" aria-label={label} onMouseMove={move} onMouseLeave={() => useStore.getState().set({ hoverIdx: null })}>
      <line className="base" x1={0} x2={w} y1={h} y2={h} />
      {warn ? <line className="warn" x1={0} x2={w} y1={y(warn)} y2={y(warn)} /> : null}
      {events.map((ev) => <line key={ev.i} className="ev" x1={ev.i} x2={ev.i} y1={0} y2={h} />)}
      {sel && <line className="ev sel" x1={sel.i} x2={sel.i} y1={0} y2={h} />}
      {series.map((s, k) => <path key={k} className={`line ${s.cls ?? ""}`} d={sparkPath(s.values, max, h)} />)}
      {hover !== null && <line className="cross" x1={hover} x2={hover} y1={0} y2={h} />}
    </svg>
  );
}

const pct = (v: number | null) => (v === null || v === undefined ? "—" : `${v.toFixed(0)} %`);

function Machine({ tl }: { tl: Timeline }) {
  const hover = useStore((s) => s.hoverIdx);
  const n = tl.windows.length;
  const keys = ([["cpu", "CPU"], ["gpu", "GPU"], ["mem", "memory"]] as const)
    .filter(([k]) => tl.windows.some((w) => w[k] !== null && w[k] !== undefined));
  const containers = Object.entries(tl.containers);
  if (!keys.length && !containers.length) return null;
  return (
    <section className="lane">
      <header>
        <b>Machine</b>
        <span className="sub">load while the probe measured · one axis, 0–100 %</span>
      </header>
      {keys.length > 0 && (
        <div className="row tallrow">
          <div className="name">host {keys.map(([, l]) => l).join(" · ")}</div>
          <Chart tall n={n} max={100} label={`host ${keys.map(([, l]) => l).join(", ")} in percent`}
            series={keys.map(([k], i) => ({ values: tl.windows.map((w) => w[k]), cls: `s${i + 1}` }))} />
          <div className="val legend-vals">
            {keys.map(([k, l], i) => (
              <div key={k}><i className={`sw s${i + 1}`} />{l} <b>{pct(valueAt(tl.windows.map((w) => w[k]), hover))}</b></div>
            ))}
          </div>
        </div>
      )}
      {containers.map(([name, values]) => (
        <div className="row" key={name}>
          <div className="name"><span className="sub">container</span> {name}</div>
          <Chart n={n} max={niceMax(values, 100)} series={[{ values }]} label={`container ${name} CPU`} />
          <div className="val">CPU <b>{pct(valueAt(values, hover))}</b></div>
        </div>
      ))}
    </section>
  );
}

function Events({ tl }: { tl: Timeline }) {
  const selected = useStore((s) => s.selected);
  const n = tl.windows.length;
  return (
    <div className="row events-row">
      <div className="name"><b>Changes</b> <span className="sub">▼ click for the answer</span></div>
      <div className="rail">
        {tl.changes.map((c) => {
          const label = c.kind === "unexplained" ? "no launch"
            : c.started.length ? `+${c.started.length} node(s)` : `−${c.stopped.length} node(s)`;
          return (
            <button key={c.id} className={`mark ${c.status.replace(" ", "-")}${selected === c.id ? " sel" : ""}`}
              style={{ left: `${xPct(c.i, n)}%` }} title={`${clock(c.t)} · ${label} · ${c.status}`}
              onClick={() => selectChange(c.id)}>
              ▼<span>{clock(c.t)} {label}</span>
            </button>
          );
        })}
      </div>
      <div className="val sub">{tl.changes.length} event(s)</div>
    </div>
  );
}

function LaneCard({ lane, n }: { lane: Lane; n: number }) {
  const [all, setAll] = useState(false);
  const sel = useSelected();
  const hit = new Set(sel?.affected.map((a) => a.topic) ?? []);
  const roots = new Set(sel?.roots.map((r) => r.topic) ?? []);
  const shown = all ? lane.topics : lane.topics.filter((t, i) => i < SHOWN || hit.has(t.name));
  const prefix = lane.topics.every((t) => t.name.startsWith(`/sensing/${lane.name}/`))
    ? `/sensing/${lane.name}/` : `/${lane.name}/`;
  return (
    <section className="lane">
      <header>
        <b>{lane.name}</b>
        {lane.changed > 0 && <Pill text={`${lane.changed} changed`} tone="warn" />}
        {lane.counts.unhealthy > 0 && <Pill text={`${lane.counts.unhealthy} unhealthy`} tone="bad" />}
        {lane.counts.degraded > 0 && <Pill text={`${lane.counts.degraded} degraded`} tone="warn" />}
        <span className="sub">{lane.topics.length} topic(s)</span>
      </header>
      {shown.map((t) => (
        <TopicRow key={t.id} t={t} n={n} label={t.name.startsWith(prefix) ? t.name.slice(prefix.length) : t.name}
          hit={hit.has(t.name)} root={roots.has(t.name)} />
      ))}
      {lane.topics.length > shown.length && (
        <button className="more" onClick={() => setAll(true)}>show {lane.topics.length - shown.length} more</button>
      )}
      {all && lane.topics.length > SHOWN && <button className="more" onClick={() => setAll(false)}>show fewer</button>}
    </section>
  );
}

function TopicRow({ t, n, label, hit, root }: { t: LaneTopic; n: number; label: string; hit: boolean; root: boolean }) {
  const hover = useStore((s) => s.hoverIdx);
  const [icon, tone] = STATUS[t.health] ?? STATUS[""];
  return (
    <div className={`row${hit ? " hit" : ""}${root ? " root" : ""}`}>
      <div className="name">
        <span className={`st ${tone}`} title={t.health || "not measured"}>{icon}</span>
        <a className="link" title={`${t.name}\nopen in the graph`} onClick={() => go(t.id)}>{label}</a>
        {root && <Pill text="root" tone="bad" />}
      </div>
      <Chart n={n} max={niceMax(t.rates, t.warn)} warn={t.warn} series={[{ values: t.rates }]} label={`${t.name}, Hz`} />
      <div className="val">
        <b>{fmtHz(valueAt(t.rates, hover))}</b> Hz
        {t.warn ? <span className="sub"> ≥ {t.warn}</span> : null}
      </div>
    </div>
  );
}
