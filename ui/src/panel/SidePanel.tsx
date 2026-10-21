// The right panel (D24, owner final touches): mode switch + environment status, search with
// dropdown, direction, history, details. Resizable from its left edge.
import { useEffect, useRef, useState } from "react";
import { go, run } from "../actions";
import { api } from "../api";
import { HEALTH, shortLabel } from "../layout";
import { type DetailsView, useStore } from "../store";
import type { DirMode, Hit } from "../types";
import { ChangesView } from "../views/Changes";
import { DoctorView, DiagView, ImpactView, KitView, MappingView, MrmView, OnlineHelp, ParamView, SwitchView, SwitchesView, WarningsView } from "../views/Others";
import { NodeView, TopicView } from "../views/TopicNode";

export function SidePanel() {
  return (
    <aside className="panel">
      <Grip />
      <Header />
      <ModeBar />
      <SearchBlock />
      <div className="details"><Details /></div>
    </aside>
  );
}

function Grip() {
  const set = (px: number) => {
    const w = Math.round(Math.min(Math.max(px, 300), window.innerWidth * 0.7));
    document.getElementById("app")?.style.setProperty("--panel-w", `${w}px`);
    try { localStorage.setItem("adviewer-panel-w", String(w)); } catch { /* private mode */ }
  };
  useEffect(() => {
    try { const w = Number(localStorage.getItem("adviewer-panel-w")); if (w) set(w); } catch { /* ignore */ }
  }, []);
  return (
    <div
      className="grip"
      title="Drag to resize · double-click to reset"
      onPointerDown={(e) => { e.currentTarget.setPointerCapture(e.pointerId); document.body.classList.add("resizing"); }}
      onPointerMove={(e) => { if (e.currentTarget.hasPointerCapture(e.pointerId)) set(window.innerWidth - e.clientX); }}
      onPointerUp={(e) => {
        e.currentTarget.releasePointerCapture(e.pointerId);
        document.body.classList.remove("resizing");
        useStore.getState().set({ fitTick: useStore.getState().fitTick + 1 });
      }}
      onDoubleClick={() => {
        document.getElementById("app")?.style.removeProperty("--panel-w");
        try { localStorage.removeItem("adviewer-panel-w"); } catch { /* ignore */ }
      }}
    />
  );
}

function Header() {
  const meta = useStore((s) => s.meta);
  const open = (view: DetailsView) => useStore.getState().set({ details: { view, id: "" } });
  return (
    <header className="top">
      <h1>AUTOWARE BACKPROPAGATOR</h1>
      <span className="badge" title={meta ? `${meta.counts.nodes} nodes · ${meta.counts.topics} topics · ${meta.counts.edges} edges · ${meta.counts.params} parameters · ${meta.layout} layout` : ""}>
        {meta?.autoware_release ? `Autoware ${meta.autoware_release}` : "release unknown"}
      </span>
      <button className="badge" title="Ingest warnings: parser gaps, unresolved items" onClick={() => open("warnings")}>
        {meta ? `${meta.counts.warnings} warnings` : "…"}
      </button>
    </header>
  );
}

function ModeBar() {
  const mode = useStore((s) => s.mode);
  const meta = useStore((s) => s.meta);
  const liveText = useStore((s) => s.liveText);
  const [doctor, setDoctor] = useState<{ status: string; err: number; warn: number } | null>(null);
  useEffect(() => {
    api.doctor().then((r) => setDoctor({
      status: r.status,
      err: r.checks.filter((c) => c.status === "error").length,
      warn: r.checks.filter((c) => c.status === "warn").length,
    })).catch(() => setDoctor({ status: "unavailable", err: 0, warn: 0 }));
  }, []);
  const st = useStore.getState;
  const screen = useStore((s) => s.screen);
  const setMode = (m: "offline" | "online") => {
    st().set({ mode: m, ...(m === "offline" ? { screen: "graph" as const } : {}) });
    if (m === "online" && !meta?.has_live) st().set({ details: { view: "online", id: "" } });
  };
  const setScreen = (s: "graph" | "dashboard") =>
    st().set({ screen: s, ...(s === "dashboard" ? { details: { view: "changes" as const, id: "" } } : {}) });
  const colour = doctor?.status === "ok" ? HEALTH.healthy : doctor?.status === "warn" ? HEALTH.degraded : HEALTH.unhealthy;
  return (
    <section className="mode">
      <div className="row">
        <div className="seg" role="group" aria-label="mode">
          <button className={mode === "offline" ? "on" : ""} onClick={() => setMode("offline")}>Offline</button>
          <button className={mode === "online" ? "on" : ""} onClick={() => setMode("online")}>Online</button>
        </div>
        {mode === "online" && (
          <div className="seg" role="group" aria-label="screen">
            <button className={screen === "graph" ? "on" : ""} onClick={() => setScreen("graph")}>Graph</button>
            <button className={screen === "dashboard" ? "on" : ""} onClick={() => setScreen("dashboard")}
              title="Live dashboard: launches, rate changes and their likely root, all sensors on one time axis">Dashboard</button>
          </div>
        )}
        <span style={{ flex: 1 }} />
        <button className="badge" title="Turn parts of Autoware on/off" onClick={() => st().set({ details: { view: "switches", id: "" } })}>Switches</button>
        {meta?.has_target && <button className="badge" onClick={() => st().set({ details: { view: "mapping", id: "" } })}>Mapping</button>}
      </div>
      <div className="status" onClick={() => st().set({ details: { view: "doctor", id: "" } })}>
        <span className="dot" style={{ background: doctor ? colour : undefined, color: doctor ? colour : undefined }} />
        {!doctor ? "checking environment…" : doctor.status === "ok" ? "environment ok"
          : doctor.status === "unavailable" ? "environment check unavailable"
            : <>environment: {doctor.err} error(s), {doctor.warn} warning(s) — <a className="link">details</a></>}
      </div>
      {mode === "online" && (
        <div className="status">
          <span className="dot" style={{ background: HEALTH[liveText?.state ?? "idle"], color: HEALTH[liveText?.state ?? "idle"] }} />
          {liveText?.text ?? (meta?.has_live ? "connecting…" : "no live data: start the probe and serve with --live")}
        </div>
      )}
    </section>
  );
}

function SearchBlock() {
  const dirMode = useStore((s) => s.dirMode);
  const history = useStore((s) => s.history);
  const root = useStore((s) => s.main.root);
  const setDir = (d: DirMode) => {
    useStore.getState().set({ dirMode: d });
    if (root) go(root);
  };
  return (
    <section className="search">
      <Search />
      <div className="row dirrow">
        <div className="seg" role="group" aria-label="direction">
          {([["back", "← Inputs", "What it needs (left)"], ["both", "↔ Both", "Inputs left, consumers right"],
            ["fwd", "Consumers →", "Who uses it (right)"]] as const).map(([d, label, title]) => (
            <button key={d} className={dirMode === d ? "on" : ""} title={title} onClick={() => setDir(d)}>{label}</button>
          ))}
        </div>
      </div>
      <div className="history">
        {history.map((h) => (
          <button key={h.id} title={h.label} onClick={() => run(h)}>
            {h.kind === "topic" || h.kind === "node" ? shortLabel(h.id) : h.label}
          </button>
        ))}
      </div>
    </section>
  );
}

function Search() {
  const meta = useStore((s) => s.meta);
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<Hit[]>([]);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const list = useRef<HTMLDivElement>(null);
  const picked = useRef(false); // the box now shows the picked name: do not search it again
  useEffect(() => {
    const query = q.trim();
    if (picked.current) { picked.current = false; return; }
    if (!query) { setHits([]); setOpen(false); return; }
    const t = setTimeout(async () => {
      const found = await api.search(query).catch(() => [] as Hit[]);
      if (meta?.has_mrm && query.length >= 2 && "mrm".startsWith(query.toLowerCase())) {
        found.unshift({ kind: "mrm", id: "mrm:", label: "MRM — diagnostics that trigger it", detail: "diagnostic graph", score: 100 });
      }
      setHits(found); setActive(0); setOpen(true);
    }, 120);
    return () => clearTimeout(t);
  }, [q, meta]);
  const pick = (h?: Hit) => {
    if (!h) return;
    picked.current = true;
    setOpen(false);
    setQ(h.label);
    run(h);
  };
  return (
    <div className="row">
      <div className="searchbox">
        <input
          id="q" type="search" autoFocus autoComplete="off" spellCheck={false} aria-label="search"
          placeholder="lidar, mrm, max_vel, /sensing/imu…" value={q}
          onChange={(e) => setQ(e.target.value)}
          onBlur={() => setTimeout(() => setOpen(false), 150)}
          onFocus={() => hits.length && setOpen(true)}
          onKeyDown={(e) => {
            if (e.key === "ArrowDown" || e.key === "ArrowUp") {
              e.preventDefault();
              if (!hits.length) return;
              const n = (active + (e.key === "ArrowDown" ? 1 : -1) + hits.length) % hits.length;
              setActive(n);
              list.current?.children[n]?.scrollIntoView({ block: "nearest" });
            } else if (e.key === "Enter") { e.preventDefault(); pick(hits[active]); }
            else if (e.key === "Escape") setOpen(false);
          }}
        />
        {open && (
          <div className="dropdown" role="listbox" ref={list}>
            {hits.length ? hits.map((h, i) => (
              <div key={h.id} className={`item${i === active ? " active" : ""}`} role="option" aria-selected={i === active}
                onMouseDown={(e) => { e.preventDefault(); pick(h); }}>
                <span className="kind">{h.kind.replace("_", " ")}</span>
                <span className="label">{h.label}</span>
                <span className="detail">{h.detail}</span>
              </div>
            )) : <div className="none">No match for “{q}”. Try part of a name: lidar, imu, mrm, trajectory.</div>}
          </div>
        )}
      </div>
      <button className="run" onClick={() => pick(hits[active])}>Run</button>
    </div>
  );
}

function Details() {
  const d = useStore((s) => s.details);
  if (!d) return <div className="sub">Details of what you click appear here.</div>;
  const views: Record<DetailsView, React.ReactNode> = {
    topic: <TopicView id={d.id} />, node: <NodeView id={d.id} />, param: <ParamView id={d.id} />,
    kit: <KitView id={d.id} />, diag: <DiagView id={d.id} />, switch: <SwitchView id={d.id} />,
    switches: <SwitchesView />, impact: <ImpactView id={d.id} />, mrm: <MrmView />, doctor: <DoctorView />,
    warnings: <WarningsView />, mapping: <MappingView />, online: <OnlineHelp />, changes: <ChangesView />,
  };
  return <div key={`${d.view}:${d.id}`} className="fade">{views[d.view]}</div>;
}
