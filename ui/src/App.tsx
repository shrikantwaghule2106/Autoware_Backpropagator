// Layout: canvas (main view + boxes) on the left, panel on the right (D24, D25, D26).
import { useEffect } from "react";
import { api } from "./api";
import { Boxes } from "./graph/Boxes";
import { GraphView } from "./graph/GraphView";
import { HEALTH, LAYERS } from "./layout";
import { SidePanel } from "./panel/SidePanel";
import { useStore } from "./store";
import { Dashboard } from "./views/Dashboard";

export function App() {
  const main = useStore((s) => s.main);
  const boxed = useStore((s) => s.boxes.length > 0);
  const theme = useStore((s) => s.theme);
  const mode = useStore((s) => s.mode);
  const hasLive = useStore((s) => !!s.meta?.has_live);
  const dashboard = useStore((s) => s.mode === "online" && s.screen === "dashboard");
  const range = useStore((s) => s.range);

  useEffect(() => { api.meta().then((meta) => useStore.getState().set({ meta })); }, []);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try { localStorage.setItem("adviewer-theme", theme); } catch { /* private mode */ }
  }, [theme]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && (e.target as HTMLElement)?.id !== "q") useStore.getState().closeTop();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  useEffect(() => { // Online mode: poll the server, which re-reads the probe dump when it changes
    if (mode !== "online" || !hasLive) return;
    const poll = async () => {
      try {
        const l = await api.live();
        if (!l.available) return useStore.getState().set({ liveText: { state: "degraded", text: "waiting for the first probe dump…" } });
        const h = l.report?.health ?? {};
        useStore.getState().set({
          health: l.health ?? {},
          liveText: {
            state: h.unhealthy ? "unhealthy" : h.degraded ? "degraded" : "healthy",
            text: `live ${l.captured_at} · ${h.healthy ?? 0} healthy · ${h.degraded ?? 0} degraded · ${h.unhealthy ?? 0} unhealthy`,
          },
        });
      } catch {
        useStore.getState().set({ liveText: { state: "unhealthy", text: "live data unavailable — connection to adviewer lost" } });
      }
    };
    poll();
    const t = setInterval(poll, 3000);
    return () => clearInterval(t);
  }, [mode, hasLive]);

  useEffect(() => { // Live dashboard (D31): the change log and lanes, re-read after each answer
    if (!dashboard || !hasLive) return;
    let alive = true;
    let timer: number | undefined;
    const every = range <= 180 ? 5000 : range <= 360 ? 10000 : 15000; // long ranges cost more
    const load = async () => {
      try {
        const tl = await api.timeline(range);
        const s = useStore.getState();
        const keep = tl.changes?.some((c) => c.id === s.selected);
        const first = tl.changes?.find((c) => c.status === "affected") ?? tl.changes?.[0];
        if (alive) s.set({ timeline: tl, selected: keep ? s.selected : first?.id ?? null });
      } catch { /* keep the last timeline; the status line reports a lost connection */ }
      if (alive) timer = window.setTimeout(load, every); // never two requests at once
    };
    load();
    return () => { alive = false; if (timer) window.clearTimeout(timer); };
  }, [dashboard, hasLive, range]);

  if (dashboard) {
    return (
      <div id="app">
        <main className="canvas" aria-label="live dashboard">
          <Dashboard />
          <Tools themeOnly />
        </main>
        <SidePanel />
      </div>
    );
  }

  return (
    <div id="app">
      <main className={`canvas${boxed ? " boxed" : ""}`} aria-label="graph">
        <div className="mainview">{main.root && <GraphView view="main" data={main} inBox={false} />}</div>
        {!main.root && (
          <div className="empty">
            <div className="big">What do you want to trace?</div>
            <div>Type part of a name on the right — <b>lidar, mrm, max_vel</b> — and press Run.</div>
            <div>The item appears with what it needs on its left. Click a <b>+n</b> to open the next level in a box.</div>
          </div>
        )}
        <Boxes />
        <Tools />
        <Legend />
        <Tip />
      </main>
      <SidePanel />
    </div>
  );
}

function Tools({ themeOnly = false }: { themeOnly?: boolean }) {
  const theme = useStore((s) => s.theme);
  const st = useStore.getState;
  return (
    <div className="tools">
      {!themeOnly && <button title="Fit the active view" onClick={() => st().set({ fitTick: st().fitTick + 1 })}>Fit</button>}
      {!themeOnly && <button title="Clear the canvas" onClick={() => { st().closeAll(); st().set({ main: { elements: [], edges: [], levels: {} }, impact: null }); }}>Clear</button>}
      <button title="Light / dark" onClick={() => st().set({ theme: theme === "dark" ? "light" : "dark" })}>{theme === "dark" ? "☾" : "☀"}</button>
    </div>
  );
}

function Legend() {
  const folded = useStore((s) => !!s.main.root);
  return (
    <details className="legend" open={!folded}>
      <summary>Legend</summary>
      <div className="grid">
        {Object.entries(LAYERS).filter(([k]) => k !== "external").map(([k, c]) => (
          <span key={k}><span className="sw" style={{ background: c, boxShadow: `0 0 8px ${c}66` }} />{k}</span>
        ))}
      </div>
      <div>▭ node card · ⬭ topic card (pill) · double border = external input (a bridge or driver provides it)</div>
      <div>- - - name convention · —— C++ source · <b>━━</b> confirmed live · <b>+n</b> opens n items in a box</div>
      <div>
        Online ring: <span style={{ color: HEALTH.healthy }}>●</span> healthy <span style={{ color: HEALTH.degraded }}>●</span> degraded{" "}
        <span style={{ color: HEALTH.unhealthy }}>●</span> unhealthy · <span style={{ color: "#f87171" }}>▒</span> red = shutdown impact
      </div>
      <div>Online: <span style={{ color: HEALTH.degraded }}>┅</span> dashed ring = changed in the change picked on the Dashboard</div>
    </details>
  );
}

function Tip() {
  const tip = useStore((s) => s.tip);
  if (!tip) return null;
  return (
    <div className="tip" style={{ left: Math.min(tip.x + 14, window.innerWidth - 470), top: tip.y + 14 }}>
      {tip.lines.map((l, i) => <div key={i}>{i === 0 ? <b>{l}</b> : l}</div>)}
    </div>
  );
}
