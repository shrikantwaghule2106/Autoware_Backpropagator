// "What changed" (D31): the change log of the Live dashboard, newest first. Each card is the
// one-level answer; open it for the root topics, their relation to the nodes that started,
// the downstream followers and the machine load, each one a link into the graph.
import { useEffect, useRef } from "react";
import { clock } from "../spark";
import { useStore } from "../store";
import type { Change, TimelineRoot } from "../types";
import { Ev, Go, Pill } from "../ui";
import { selectChange } from "./Dashboard";

const KIND: Record<Change["kind"], string> = { launch: "nodes started", stop: "nodes stopped", unexplained: "no launch event" };
const RELATION: Record<TimelineRoot["relation"], [string, string]> = {
  "data path": ["data path", "bad"], "new reader": ["new reader", "warn"], unconnected: ["not connected", ""],
};

export function ChangesView() {
  const tl = useStore((s) => s.timeline);
  const selected = useStore((s) => s.selected);
  if (!tl?.available) {
    return <div className="sub">Changes appear here once the probe has recorded a few windows (Live dashboard).</div>;
  }
  return (
    <>
      <h2>What changed</h2>
      <div className="sub">
        Newest first. Each answer says what changed at the same moment and how it is connected in the
        graph: a lead for the deep dive, not proof of the cause. Click a topic to open it in the graph.
      </div>
      {!tl.changes.length && <div className="callout ok">No launch events and no rate changes in this range.</div>}
      {tl.changes.map((c) => <ChangeCard key={c.id} c={c} open={c.id === selected} />)}
    </>
  );
}

function ChangeCard({ c, open }: { c: Change; open: boolean }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => { // a change picked on the dashboard's rail shows its answer
    if (open) ref.current?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [open]);
  return (
    <div ref={ref} className={`change ${c.status.replace(" ", "-")}${open ? " open" : ""}`} onClick={() => selectChange(c.id)}>
      <div className="head">
        <b>{clock(c.t)}</b> <Pill text={KIND[c.kind]} />
        <Pill text={c.status} tone={c.status === "affected" ? "warn" : c.status === "no effect" ? "ok" : ""} />
      </div>
      <p className="answer">{c.answer}</p>
      {open && <Detail c={c} />}
    </div>
  );
}

function NodeList({ title, nodes }: { title: string; nodes: string[] }) {
  if (!nodes.length) return null;
  return (
    <>
      <h3>{title} ({nodes.length})</h3>
      <div className="links">{nodes.slice(0, 12).map((n) => <Go key={n} id={"n:" + n}>{n}</Go>)}</div>
      {nodes.length > 12 && <div className="sub">and {nodes.length - 12} more</div>}
    </>
  );
}

function Detail({ c }: { c: Change }) {
  const res = c.resources;
  const launched = c.started.length + c.stopped.length > 0; // a relation needs nodes that started or stopped
  const rows = ([["cpu", "host CPU"], ["gpu", "GPU"], ["mem", "memory"]] as const).filter(([k]) => res[k]);
  return (
    <div className="detail" onClick={(e) => e.stopPropagation()}>
      {c.roots.length > 0 && <h3>Root topic(s)</h3>}
      {c.roots.map((r) => {
        const [rel, tone] = RELATION[r.relation];
        return (
          <div className="rootrow" key={r.topic}>
            <div>
              <Go id={"t:" + r.topic} />{" "}
              <b>{r.kind === "stopped" ? "stopped" : `${r.before_hz} → ${r.after_hz} Hz`}</b>
              {r.kind === "health" && <> · {r.health_before} → {r.health_after}</>}{" "}
              {launched && <Pill text={rel} tone={tone} />}
            </div>
            {r.node && (
              <div className="sub">
                {r.relation === "data path" ? "fed by " : "read by "}<Go id={"n:" + r.node}>{r.node}</Go>
                {r.path.length > 1 && <> via {r.path.map((t, i) => <span key={t}>{i ? " → " : ""}<Go id={"t:" + t} /></span>)}</>}
              </div>
            )}
            {r.followers.length > 0 && (
              <details>
                <summary className="sub">{r.followers.length} downstream topic(s) followed</summary>
                <div className="links">{r.followers.map((t) => <Go key={t} id={"t:" + t} />)}</div>
              </details>
            )}
          </div>
        );
      })}
      <NodeList title="Started" nodes={c.started} />
      <NodeList title="Stopped" nodes={c.stopped} />
      {c.added_topics.length > 0 && (
        <div className="sub">
          {c.added_topics.length} new topic(s), {(c.added_bandwidth_Bps / 1e6).toFixed(2)} MB/s more on the network
        </div>
      )}
      {(rows.length > 0 || res.containers?.length) && (
        <>
          <h3>Machine, before → after</h3>
          <table>
            <tbody>
              {rows.map(([k, label]) => <tr key={k}><td>{label}</td><td>{res[k]![0]} → {res[k]![1]} %</td></tr>)}
              {res.containers?.map((x) => (
                <tr key={x.name}><td>container {x.name}</td><td>CPU {x.cpu[0]} → {x.cpu[1]} %</td></tr>
              ))}
            </tbody>
          </table>
        </>
      )}
      <Ev>{c.evidence}</Ev>
    </div>
  );
}
