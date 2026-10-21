// Small shared pieces of the details panel.
import { useEffect, useState } from "react";
import { go } from "./actions";
import type { Endpoint, Qos, Rate } from "./types";

/** Fetch on mount / when deps change; returns [data, error]. */
export function useFetch<T>(load: () => Promise<T>, deps: unknown[]): [T | undefined, string] {
  const [data, setData] = useState<T>();
  const [error, setError] = useState("");
  useEffect(() => {
    let alive = true;
    setData(undefined);
    setError("");
    load().then((d) => alive && setData(d)).catch((e: Error) => alive && setError(e.message));
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return [data, error];
}

export function Loading({ error }: { error?: string }) {
  return error ? <div className="callout bad">{error}</div> : <div><span className="spinner" /> loading…</div>;
}

export function Go({ id, children }: { id: string; children?: React.ReactNode }) {
  return <a className="link" onClick={() => go(id)}>{children ?? id.replace(/^[a-z]+:/, "")}</a>;
}

export function Pill({ text, tone = "" }: { text: string; tone?: string }) {
  return <span className={`pill ${tone}`}>{text}</span>;
}

export function Ev({ children }: { children: React.ReactNode }) {
  return <span className="ev">{children}</span>;
}

export function Quote({ text, source }: { text: string; source: string }) {
  return <div className="quote">{text}<Ev>{source}</Ev></div>;
}

export function Copy({ text }: { text: string }) {
  const [done, setDone] = useState(false);
  return (
    <a className="link copy" onClick={() => navigator.clipboard?.writeText(text).then(() => setDone(true))}>
      {done ? "copied" : "copy"}
    </a>
  );
}

export function QosText({ qos }: { qos?: Qos }) {
  if (!qos || !Object.keys(qos).length) return <span className="sub">unknown</span>;
  const keys = (["reliability", "durability", "history", "depth"] as const).filter((k) => qos[k]);
  return (
    <span>
      {keys.map((k, i) => <span key={k}>{i ? " · " : ""}{k} <b>{qos[k]}</b></span>)}
      {qos.as_written && <Ev>C++: {qos.as_written}</Ev>}
    </span>
  );
}

export function RatesTable({ rates }: { rates: Rate[] }) {
  if (!rates.length) return <div className="sub">No expected rate found (no timer literal, rate parameter or monitor).</div>;
  return (
    <table>
      <thead><tr><th>Hz</th><th>source</th><th>where</th></tr></thead>
      <tbody>
        {rates.map((r, i) => (
          <tr key={i}><td>{r.hz ?? "?"}</td><td><Pill text={r.kind} /></td><td>{r.note}<Ev>{r.evidence}</Ev></td></tr>
        ))}
      </tbody>
    </table>
  );
}

export function Endpoints({ list }: { list: Endpoint[] }) {
  if (!list.length) return <div className="sub">none found</div>;
  return (
    <table>
      <tbody>
        {list.map((e, i) => (
          <tr key={i}>
            <td><Go id={"n:" + e.node} /> <Pill text={e.confidence} /><Ev>{e.evidence}</Ev></td>
            <td style={{ width: "42%" }}><QosText qos={e.qos} /></td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export const tone = (status: string) =>
  ({ ok: "ok", healthy: "ok", true: "ok", warn: "warn", degraded: "warn", error: "bad", unhealthy: "bad" })[status] ?? "";
