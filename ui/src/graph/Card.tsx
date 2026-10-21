// One topic or node as a raised card (D26, D27): layer dot + name, type/package + layer,
// health ring (Online), red tint (impact), "+n" / "−" button on its growth side (D25).
import { Handle, type Node, type NodeProps, Position } from "@xyflow/react";
import { itemClicked, plusClicked } from "../actions";
import { LAYERS, shortLabel, shortType } from "../layout";
import type { Dir, Element } from "../types";

export interface CardData extends Record<string, unknown> {
  el: Element;
  view: string;
  isRoot: boolean;
  plus: { dir: Dir; count: number; open: boolean } | null;
  health?: string;
  impacted: boolean;
  changed?: boolean; // in the change selected on the Live dashboard (D31)
  focused: boolean;
}
export type CardNode = Node<CardData, "card">;

export function Card({ data }: NodeProps<CardNode>) {
  const { el, view, isRoot, plus, health, impacted, focused, changed } = data;
  const sub = el.kind === "topic" ? shortType(el.msg_type) || "type unknown" : el.package || "package unknown";
  const classes = [
    "card", el.kind, isRoot && "root", el.external && "external", health && `health-${health}`,
    impacted && "impacted", focused && "focused", changed && "changed",
  ].filter(Boolean).join(" ");
  const counts = `${el.inputs} input(s) · ${el.consumers} consumer(s)`;
  return (
    <div
      className={classes}
      style={{ "--layer": LAYERS[el.layer] ?? LAYERS.unknown } as React.CSSProperties}
      title={`${el.label}\n${el.kind} · ${el.layer}${el.msg_type ? "\n" + el.msg_type : ""}${el.external ? "\nexternal input — a bridge or driver provides it" : ""}\n${counts}`}
      onClick={() => itemClicked(view, el.id)}
    >
      <Handle type="target" position={Position.Left} isConnectable={false} />
      <div className="name"><span className="dot" />{shortLabel(el.id)}</div>
      <div className="sub">{sub} · <span className="layer">{el.layer}</span></div>
      {plus && (
        <button
          className={`plus ${plus.dir}${plus.open ? " open" : ""}`}
          title={plus.open ? "close this box" : `open ${plus.count} ${plus.dir === "back" ? "input(s)" : "consumer(s)"} in a box`}
          onClick={(e) => { e.stopPropagation(); plusClicked(view, el.id, plus.dir); }}
        >
          {plus.open ? "−" : `+${plus.count}`}
        </button>
      )}
      <Handle type="source" position={Position.Right} isConnectable={false} />
    </div>
  );
}
