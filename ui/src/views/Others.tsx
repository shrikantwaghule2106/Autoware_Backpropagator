// Parameter (FR-7), sensor kit, diagnostic unit, switches, impact (FR-4), MRM, doctor (FR-12),
// warnings, mapping (Phase 5) and the Online-mode help.
import { useEffect } from "react";
import { api } from "../api";
import { useStore } from "../store";
import type { GateNode } from "../types";
import { Copy, Ev, Go, Loading, Pill, Quote, tone, useFetch } from "../ui";

const toImpact = (id: string) => useStore.getState().set({ details: { view: "impact", id } });

export function ParamView({ id }: { id: string }) {
  const [p, error] = useFetch(() => api.param(id), [id]);
  if (!p) return <Loading error={error} />;
  return (
    <>
      <h2>{id}</h2>
      <div className="sub">parameter · where it is set and how to change it (FR-7)</div>
      {p.entries.map((e, i) => (
        <div key={i}>
          <h3>{e.name} = <code>{e.value}</code> <Pill text={e.kind} /></h3>
          <Ev>{e.evidence}</Ev>
          {e.package && <span className="sub">package {e.package}</span>}
          {e.nodes.length > 0 && (
            <div className="sub" style={{ marginTop: 4 }}>
              used by {e.nodes.map((n, j) => <span key={n}>{j ? ", " : ""}<Go id={"n:" + n} /></span>)}
            </div>
          )}
          {e.via_arg
            ? <div className="sub">controlled by launch argument <code>{e.via_arg}</code>{e.via_expr && ` — ${e.via_expr}`}</div>
            : e.kind !== "launch-arg" && <div className="callout">No launch argument reaches this value: change the file itself.</div>}
          <table><tbody>{e.routes.map((r, j) => (
            <tr key={j}><td style={{ width: "30%" }}><b>{r.route}</b></td><td><code>{r.how}</code> <Copy text={r.how} /></td></tr>
          ))}</tbody></table>
        </div>
      ))}
    </>
  );
}

export function KitView({ id }: { id: string }) {
  const [k, error] = useFetch(() => api.kit(id), [id]);
  if (!k) return <Loading error={error} />;
  return (
    <>
      <h2>{id}</h2>
      <div className="sub">sensor kit · launch packages {k.packages.map((p) => <code key={p}>{p} </code>)}</div>
      <div className="actions"><button onClick={() => toImpact("kit:" + id)}>If the kit is shut down…</button></div>
      <h3>Nodes ({k.nodes.length})</h3>
      {k.nodes.map((n) => <div key={n}><Go id={"n:" + n} /></div>)}
    </>
  );
}

export function DiagView({ id }: { id: string }) {
  const [u, error] = useFetch(() => api.diag(id), [id]);
  if (!u) return <Loading error={error} />;
  return (
    <>
      <h2>{u.path}</h2>
      <div className="sub">diagnostic unit · <Pill text={u.type} /> · <Ev>{u.source_file}</Ev></div>
      {u.diag_name && <><h3>Watches /diagnostics status</h3><code>{u.diag_name}</code></>}
      <h3>Children</h3>
      {u.children.length
        ? u.children.map((c) => <div key={c}>{c.includes("#") || c.startsWith("__") ? <code>{c}</code> : <Go id={"d:" + c}>{c}</Go>}</div>)
        : <div className="sub">none</div>}
    </>
  );
}

export function SwitchesView() {
  const [list, error] = useFetch(() => api.switches(), []);
  if (!list) return <Loading error={error} />;
  const table = (rows: typeof list) => (
    <table>
      <thead><tr><th>switch</th><th>now</th><th>nodes</th></tr></thead>
      <tbody>{rows.map((s) => (
        <tr key={s.name}>
          <td><Go id={"s:" + s.name}>{s.name}</Go><Ev>{s.cli_arg ? s.command : `fixed in a launch file: ${s.declared || "?"}`}</Ev></td>
          <td><Pill text={s.value || "?"} tone={/^(true|1)$/i.test(s.value) ? "ok" : ""} /></td>
          <td>{s.node_count}</td>
        </tr>
      ))}</tbody>
    </table>
  );
  return (
    <>
      <h2>Switches — make Autoware lighter</h2>
      <div className="sub">
        Launch arguments that turn parts of Autoware on or off, read from the launch files (if= / unless=).
        With a command shown, set it when launching; otherwise edit the launch file at the line given.
      </div>
      <div className="callout">Turning a part off can starve other parts of their inputs — open a switch and check “What breaks if it is off”.</div>
      <h3>On / off ({list.filter((s) => s.boolean).length})</h3>{table(list.filter((s) => s.boolean))}
      <h3>Mode selectors ({list.filter((s) => !s.boolean).length})</h3>{table(list.filter((s) => !s.boolean))}
    </>
  );
}

export function SwitchView({ id }: { id: string }) {
  const [s, error] = useFetch(() => api.switch(id), [id]);
  if (!s) return <Loading error={error} />;
  return (
    <>
      <h2>{s.name} = <code>{s.value}</code></h2>
      <div className="sub">switch · {s.node_count} node(s) run because of it · declared at <Ev>{s.declared || "?"}</Ev></div>
      {s.cli_arg
        ? <><h3>Change it when launching</h3><code>{s.command}</code> <Copy text={s.command} />{s.cli_chain && <Ev>reaches it via {s.cli_chain}</Ev>}</>
        : <div className="callout">No command-line argument reaches it: edit the launch file at the line above.</div>}
      <div className="actions">
        {s.node_count > 0 && <button onClick={() => toImpact("switch:" + s.name)}>What breaks if it is off</button>}
      </div>
      <h3>What it gates ({s.gates.length})</h3>
      <table><tbody>{s.gates.map((g, i) => (
        <tr key={i}>
          <td><Pill text={g.on === null ? "undecided" : g.on ? "on" : "off"} tone={g.on ? "ok" : ""} /></td>
          <td>
            <b>{g.tag}</b> {g.target}<Ev>{g.evidence} · {g.condition}</Ev>
            {g.nodes.map((n, j) => <span key={n}>{j ? ", " : ""}<Go id={"n:" + n} /></span>)}
          </td>
        </tr>
      ))}</tbody></table>
    </>
  );
}

export function ImpactView({ id }: { id: string }) {
  const [r, error] = useFetch(() => api.impact(id), [id]);
  useEffect(() => {
    if (r) {
      useStore.getState().set({
        impact: { nodes: new Set(r.affected_nodes.map((a) => "n:" + a.id)), topics: new Set(r.stopped_topics.map((t) => "t:" + t)) },
      });
    }
  }, [r]);
  if (!r) return <Loading error={error} />;
  const hops = new Map<number, typeof r.affected_nodes>();
  for (const a of r.affected_nodes) hops.set(a.hop, [...(hops.get(a.hop) ?? []), a]);
  const m = r.mrm;
  return (
    <>
      <h2>If {id.replace(/^[a-z]+:/, "")} stops</h2>
      <div className="callout">{r.note}</div>
      {m && (
        <div className={`callout ${m.triggered ? "bad" : "ok"}`}>
          <b>{m.triggered ? "MRM would be triggered" : "MRM not triggered"}</b>
          {m.triggered && <> — behaviour: <b>{m.behaviour}</b></>}
          <div>modes: {Object.entries(m.modes).map(([k, v]) => (
            <span key={k}>{k} <Pill text={v ? "available" : "unavailable"} tone={v ? "ok" : "bad"} /> </span>
          ))}</div>
          <div className="sub" style={{ marginTop: 4 }}>{m.rule}</div>
          {m.evidence.filter(Boolean).map((e) => <Ev key={e}>{e}</Ev>)}
        </div>
      )}
      <h3>Failed diagnostics ({r.failed_diagnostics.length})</h3>
      {r.failed_diagnostics.length
        ? r.failed_diagnostics.map((d) => <div key={d.path}><Go id={"d:" + d.path}>{d.path}</Go><Ev>{d.diag_name}</Ev></div>)
        : <div className="sub">none</div>}
      <h3>Affected nodes ({r.affected_nodes.length})</h3>
      {[...hops].map(([h, list]) => (
        <div key={h}>
          <div className="sub" style={{ marginTop: 6 }}>{h === 0 ? "stopped" : `hop ${h}`}</div>
          {list.map((a) => <div key={a.id}><Go id={"n:" + a.id} />{a.hop > 0 && <span className="sub"> via {a.via}</span>}</div>)}
        </div>
      ))}
      <h3>Stopped topics ({r.stopped_topics.length})</h3>
      {r.stopped_topics.map((t) => <div key={t}><Go id={"t:" + t} /></div>)}
    </>
  );
}

function Gates({ node }: { node: GateNode }) {
  return (
    <>
      {node.type === "diag"
        ? <><Pill text="diag" /> <code>{node.diag_name}</code>{node.node && <> → <Go id={"n:" + node.node} /></>}</>
        : <><Pill text={node.type} /> {!node.path.startsWith("__") && <Go id={"d:" + node.path}>{node.path}</Go>}</>}
      {node.children?.length > 0 && <ul className="tree">{node.children.map((c, i) => <li key={i}><Gates node={c} /></li>)}</ul>}
    </>
  );
}

export function MrmView() {
  const [g, error] = useFetch(() => api.mrm(), []);
  if (!g) return <Loading error={error} />;
  return (
    <>
      <h2>MRM — what gates it</h2>
      <div className="sub">MRM starts when mode <b>{g.mode}</b> (unit {g.unit ? <Go id={"d:" + g.unit}>{g.unit}</Go> : "?"}) is not OK.</div>
      <div className="callout">
        {g.rules.availability_rule}<Ev>{g.rules.availability_evidence}</Ev>
        {g.rules.trigger_rule}<Ev>{g.rules.trigger_evidence}</Ev>
        {g.rules.behaviour_rule}<Ev>{g.rules.behaviour_evidence}</Ev>
      </div>
      <h3>Chain</h3>
      {g.chain.map((t, i) => <span key={t}>{i ? " → " : ""}<Go id={"t:" + t} /></span>)}
      <h3>Diagnostic tree (and = every child must be OK, or = one is enough)</h3>
      {g.tree && <Gates node={g.tree} />}
    </>
  );
}

export function DoctorView() {
  const [r, error] = useFetch(() => api.doctor(), []);
  if (!r) return <Loading error={error} />;
  return (
    <>
      <h2>Environment check</h2>
      <div className="sub">adviewer doctor · overall <Pill text={r.status} tone={tone(r.status)} /></div>
      <table><tbody>{r.checks.map((c) => (
        <tr key={c.name}>
          <td><Pill text={c.status} tone={tone(c.status)} /></td>
          <td><b>{c.name}</b><br />{c.detail}{c.fix && <div style={{ marginTop: 3 }}><code>{c.fix}</code> <Copy text={c.fix} /></div>}</td>
        </tr>
      ))}</tbody></table>
    </>
  );
}

export function WarningsView() {
  const [w, error] = useFetch(() => api.warnings(), []);
  if (!w) return <Loading error={error} />;
  return (
    <>
      <h2>Ingest warnings ({w.length})</h2>
      <div className="sub">Parser gaps and unresolved items — nothing is dropped silently.</div>
      {w.slice(0, 400).map((x, i) => <Ev key={i}>{x}</Ev>)}
    </>
  );
}

export function MappingView() {
  const [m, error] = useFetch(() => api.mapping(), []);
  if (!m) return <Loading error={error} />;
  return (
    <>
      <h2>Mapping — {m.target}</h2>
      <div className="sub">
        coverage <b>{Math.round(m.coverage * 100)} %</b> of {m.required_count} required inputs · export{" "}
        <a className="link" href="/api/mapping.md" target="_blank" rel="noreferrer">checklist (md)</a> ·{" "}
        <a className="link" href="/api/mapping" target="_blank" rel="noreferrer">json</a>
      </div>
      <h3>Readiness per subsystem</h3>
      {Object.entries(m.readiness).map(([k, r]) => (
        <div key={k} style={{ margin: "6px 0" }}>
          {k} <span className="sub">{r.readiness === null ? "n/a" : `${Math.round(r.readiness * 100)} %`} · <Go id={"t:" + r.topic}>{r.topic}</Go></span>
          <div className="bar"><i style={{ width: `${(r.readiness ?? 0) * 100}%` }} /></div>
        </div>
      ))}
      <h3>Assigned ({m.pairs.length})</h3>
      <table><tbody>{m.pairs.map((p) => (
        <tr key={p.signal}><td><b>{p.signal}</b></td><td><Go id={"t:" + p.topic} />
          <Ev>cost {p.cost} · {Object.entries(p.terms).map(([k, v]) => `${k} ${v}`).join(" · ")}</Ev></td></tr>
      ))}</tbody></table>
      <h3>Not provided yet ({m.unassigned_required.length})</h3>
      {m.unassigned_required.map((t) => <div key={t}><Go id={"t:" + t} /></div>)}
      {m.unused_signals.length > 0 && <><h3>Signals not used</h3>{m.unused_signals.join(", ")}</>}
    </>
  );
}

export function OnlineHelp() {
  return (
    <>
      <h2>Online mode</h2>
      <div className="sub">Live health needs the C++ probe next to a running Autoware (Linux).</div>
      <h3>1 · build and run the probe</h3>
      <code>adviewer probe build</code><br /><code>adviewer probe run</code>
      <div className="sub">
        Autoware in Docker: <code>adviewer probe run --like &lt;autoware-container&gt;</code> · built on this machine:{" "}
        <code>adviewer probe build --native</code> then <code>adviewer probe run --native</code>
      </div>
      <h3>2 · serve with the probe's dump</h3>
      <code>adviewer up --online</code> <span className="sub">(or adviewer serve graph.json --live probe_dump.json)</span>
      <Quote text="Healthy topics get a green ring, degraded amber, unhealthy a pulsing red ring." source="D24 / FR-9" />
      <h3>3 · Dashboard</h3>
      <div className="sub">
        <code>adviewer probe run</code> also keeps every probe window. Switch to <b>Dashboard</b> (next to
        Online) to see every sensor and layer on one time axis, the nodes that started or stopped, and for each
        change the root topic and how it is connected to them. <code>adviewer up --online</code> keeps that history
        itself, whichever way the probe runs.
      </div>
    </>
  );
}
