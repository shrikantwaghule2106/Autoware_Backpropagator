// Stacked boxes (D25): the first opens large; deeper boxes are smaller, inside it, cascaded
// and aligned; only the top box is active. ✕ closes the top box only.
import { useEffect, useRef, useState } from "react";
import { go } from "../actions";
import { api } from "../api";
import { shortLabel } from "../layout";
import { type Box, type ViewData, useStore, viewKey } from "../store";
import { GraphView } from "./GraphView";

function geometry(depth: number): React.CSSProperties {
  if (depth === 1) return { left: "2.5%", top: 56, width: "95%", height: "calc(100% - 72px)" };
  const k = depth - 2;
  return { right: `calc(4% + ${k * 22}px)`, top: 116 + k * 32, width: "58%", height: "62%" };
}

export function Boxes() {
  const boxes = useStore((s) => s.boxes);
  const root = useStore((s) => s.main.root);
  return (
    <div className="boxes">
      {boxes.map((b, i) => {
        const trail = [root, ...boxes.slice(0, i + 1).map((x) => x.eid)]
          .filter((id): id is string => !!id)
          .filter((id, j, all) => id !== all[j - 1])
          .map(shortLabel)
          .join(" › ");
        return <BoxPanel key={b.key} box={b} depth={i + 1} active={i === boxes.length - 1} trail={trail} />;
      })}
    </div>
  );
}

function BoxPanel({ box, depth, active, trail }: { box: Box; depth: number; active: boolean; trail: string }) {
  const [data, setData] = useState<ViewData | null>(null);
  const [error, setError] = useState("");
  const closing = useRef(false);
  const flash = useStore((s) => s.flash);
  const panel = useRef<HTMLElement>(null);

  useEffect(() => {
    api.expand(box.eid, box.dir).then((res) => {
      const levels: Record<string, number> = { [box.eid]: 0 };
      res.nodes.forEach((n) => { levels[n.id] = box.dir === "back" ? -1 : 1; });
      setData({ root: box.eid, elements: [res.root, ...res.nodes], edges: res.edges, levels });
    }).catch((e: Error) => setError(e.message));
  }, [box.eid, box.dir]);

  useEffect(() => { // a click on a view below: show which box is in charge
    if (active && flash && panel.current) {
      panel.current.animate([{ boxShadow: "0 0 0 2px var(--accent)" }, { boxShadow: "none" }], { duration: 500 });
    }
  }, [flash, active]);

  const count = data ? data.elements.length - 1 : 0;
  return (
    <section ref={panel} className={`box${active ? "" : " inactive"}`} style={geometry(depth)}>
      <header>
        <div className="title">
          {box.dir === "back" ? "Inputs of" : "Consumers of"} <b>{box.eid.replace(/^[tn]:/, "")}</b>
          <span className="trail">{trail}</span>
        </div>
        <button className="focus" title="Make this the main view" onClick={() => go(box.eid)}>⤢</button>
        <button
          className="close"
          title="Close this box (Esc)"
          onClick={() => { if (!closing.current) { closing.current = true; useStore.getState().closeTop(); } }}
        >✕</button>
      </header>
      <div className="boxgraph">
        {data ? <GraphView view={viewKey(box)} data={data} inBox /> : <div className="loading">{error || <span className="spinner" />}</div>}
      </div>
      <div className="count">
        {count} {box.dir === "back" ? "input(s)" : "consumer(s)"} · click an item for details · +n opens the next level · Esc closes
      </div>
    </section>
  );
}
