// Topic and node details (FR-5, FR-6) with "About this topic" (Autoware's own words + computed facts).
import { useState } from "react";
import { openBoxFor } from "../actions";
import { api } from "../api";
import { clock } from "../spark";
import { useStore } from "../store";
import type { TopicDetails } from "../types";
import { Endpoints, Ev, Go, Loading, Pill, QosText, Quote, RatesTable, tone, useFetch } from "../ui";

function About({ about, id }: { about: NonNullable<TopicDetails["about"]>; id: string }) {
  const st = about.if_it_stops;
  return (
    <div className="about">
      <h3>About this topic</h3>
      {about.docs.length
        ? about.docs.map((d, i) => (
          <Quote key={i} text={d.text} source={`${d.package} README — ${d.role === "output" ? "publishes it" : "reads it"} · ${d.source}`} />
        ))
        : <div className="sub">No README of the packages that use it describes this topic.</div>}
      {about.message.description && (
        <Quote text={about.message.description} source={`message ${about.message.type} · ${about.message.source}`} />
      )}
      <ul className="role">{about.role.map((r, i) => <li key={i}>{r}</li>)}</ul>
      <div className={`callout${st.mrm ? " bad" : ""}`}>
        If it stops: {st.affected_nodes} node(s) affected, {st.failed_diagnostics} diagnostic(s) fail
        {st.mrm === true && <> — <b>MRM would start ({st.mrm_behaviour})</b></>}
        {st.mrm === false && " — no MRM"}.{" "}
        <a className="link" onClick={() => useStore.getState().set({ details: { view: "impact", id } })}>see what</a>
      </div>
    </div>
  );
}

export function TopicView({ id }: { id: string }) {
  const [t, error] = useFetch(() => api.topic("t:" + id), [id]);
  if (!t) return <Loading error={error} />;
  const live = t.live?.measured ? t.live : null;
  return (
    <>
      <h2>{t.id} {t.health && <Pill text={t.health} tone={tone(t.health)} />}</h2>
      <div className="sub">
        topic · {t.layer}
        {t.external && <> · <b>external input</b>: nothing inside Autoware publishes it — a bridge or driver must</>}
      </div>
      <ChangedNote id={id} />
      {t.about && <About about={t.about} id={"t:" + id} />}
      <div className="actions">
        {t.publishers.length > 0 && <button onClick={() => openBoxFor("t:" + id, "back")}>Publishers in a box ({t.publishers.length})</button>}
        {t.subscribers.length > 0 && <button onClick={() => openBoxFor("t:" + id, "fwd")}>Subscribers in a box ({t.subscribers.length})</button>}
        <button onClick={() => useStore.getState().set({ details: { view: "impact", id: "t:" + id } })}>If it stops…</button>
      </div>
      <h3>Message type</h3>
      <div>
        {t.msg_type ? <code>{t.msg_type}</code> : <span className="sub">unknown (no literal type in C++ / not live)</span>}
        {t.schema && (
          <details><summary>{t.schema.fields.length} fields</summary>
            <table><tbody>{t.schema.fields.map((f) => (
              <tr key={f.name}><td>{f.name}</td><td><code>{f.type}{f.is_array ? "[]" : ""}</code></td></tr>
            ))}</tbody></table>
          </details>
        )}
      </div>
      <h3>QoS (publisher)</h3><div><QosText qos={t.qos} /></div>
      <h3>Expected rate</h3><RatesTable rates={t.rates} />
      {live && (
        <>
          <h3>Live (last window)</h3>
          <table><tbody>
            <tr><td>rate</td><td><b>{Number(live.rate_hz).toFixed(2)} Hz</b> ({String(live.count)} msgs)</td></tr>
            <tr><td>jitter / max gap</td><td>{Number(live.jitter_ms).toFixed(2)} ms / {Number(live.max_gap_ms).toFixed(1)} ms</td></tr>
            <tr><td>bandwidth</td><td>{(Number(live.bandwidth_Bps) / 1e6).toFixed(3)} MB/s · {Math.round(Number(live.mean_size_B))} B/msg</td></tr>
          </tbody></table>
        </>
      )}
      <h3>Publishers ({t.publishers.length})</h3><Endpoints list={t.publishers} />
      <h3>Subscribers ({t.subscribers.length})</h3><Endpoints list={t.subscribers} />
      <h3>Declared in</h3>
      {t.declared_in.length ? t.declared_in.map((f) => <Ev key={f}>{f}</Ev>) : <span className="sub">—</span>}
    </>
  );
}

export function NodeView({ id }: { id: string }) {
  const [n, error] = useFetch(() => api.node("n:" + id), [id]);
  const [filter, setFilter] = useState("");
  if (!n) return <Loading error={error} />;
  const io = (list: NonNullable<typeof n>["inputs"]) => list.length ? (
    <table><tbody>{list.map((e, i) => (
      <tr key={i}>
        <td><Go id={"t:" + e.topic} /> <Pill text={e.confidence} /><Ev>{e.evidence}</Ev></td>
        <td style={{ width: "40%" }}><QosText qos={e.qos} /></td>
      </tr>
    ))}</tbody></table>
  ) : <div className="sub">none found</div>;
  return (
    <>
      <h2>{n.id}</h2>
      <div className="sub">
        node · {n.layer} · package <code>{n.package}</code>{n.executable && <> · <code>{n.executable}</code></>}
      </div>
      <div className="sub">launched from <Ev>{n.source_file}</Ev></div>
      {n.docs.map((d, i) => <Quote key={i} text={d.text} source={`${d.package} README · ${d.source}`} />)}
      <div className="actions">
        {n.inputs.length > 0 && <button onClick={() => openBoxFor("n:" + id, "back")}>Inputs in a box ({n.inputs.length})</button>}
        {n.outputs.length > 0 && <button onClick={() => openBoxFor("n:" + id, "fwd")}>Outputs in a box ({n.outputs.length})</button>}
        <button onClick={() => useStore.getState().set({ details: { view: "impact", id: "n:" + id } })}>If it stops…</button>
      </div>
      <h3>Inputs ({n.inputs.length})</h3>{io(n.inputs)}
      <h3>Outputs ({n.outputs.length})</h3>{io(n.outputs)}
      <h3>Timers and rates</h3><RatesTable rates={n.rates} />
      <h3>Diagnostics it reports</h3>
      {n.diagnostics.length
        ? n.diagnostics.map((d) => <div key={d}><Go id={"d:" + d} /></div>)
        : <div className="sub">none matched in the diagnostic graph</div>}
      <h3>Parameters ({n.param_count})</h3>
      <input className="filter" placeholder="filter…" value={filter} onChange={(e) => setFilter(e.target.value.toLowerCase())} />
      <table><tbody>
        {n.params.filter((p) => p.name.toLowerCase().includes(filter)).map((p, i) => (
          <tr key={i}><td><Go id={"p:" + p.name}>{p.name}</Go></td><td><code>{p.value}</code><Ev>{p.evidence}</Ev></td></tr>
        ))}
      </tbody></table>
      {n.param_count > n.params.length && <div className="sub">first {n.params.length} shown</div>}
    </>
  );
}

/** Online: what the Live dashboard's selected change did to this topic (D31). */
function ChangedNote({ id }: { id: string }) {
  const change = useStore((s) => (s.mode === "online" ? s.timeline?.changes.find((c) => c.id === s.selected) : undefined));
  const hit = change?.affected.find((a) => a.topic === id);
  if (!change || !hit) return null;
  const root = change.roots.some((r) => r.topic === id);
  return (
    <div className="callout">
      Changed at <b>{clock(change.t)}</b>:{" "}
      {hit.kind === "stopped" ? "stopped" : <>{hit.before_hz} → {hit.after_hz} Hz (−{Math.round(hit.drop * 100)} %)</>}
      {root ? " · the root of that change" : " · followed the root of that change"}. Its health ring
      only uses Autoware's own thresholds.{" "}
      <a className="link" onClick={() => useStore.getState().set({ screen: "dashboard", details: { view: "changes", id: "" } })}>
        back to the dashboard
      </a>
    </div>
  );
}
