// A graph of one view (the main canvas or a box). Positions come from layout.ts; new cards
// start on their parent and slide out (CSS transition on the node transform), then the
// camera fits — the D25 motion.
import {
  Background, BackgroundVariant, Controls, type Edge, MarkerType, ReactFlow, ReactFlowProvider, useReactFlow,
} from "@xyflow/react";
import { useEffect, useMemo, useRef, useState } from "react";
import { layout, plusFor } from "../layout";
import { type ViewData, topView, useStore } from "../store";
import type { FlowEdge } from "../types";
import { Card, type CardNode } from "./Card";

const nodeTypes = { card: Card };
const SLIDE_MS = 380;
const RANK: Record<string, number> = { unhealthy: 3, degraded: 2, healthy: 1, idle: 0 };

export function GraphView(props: { view: string; data: ViewData; inBox: boolean }) {
  return (
    <ReactFlowProvider>
      <Inner {...props} />
    </ReactFlowProvider>
  );
}

function Inner({ view, data, inBox }: { view: string; data: ViewData; inBox: boolean }) {
  const rf = useReactFlow();
  const box = useRef<HTMLDivElement>(null);
  const [height, setHeight] = useState(600);
  const [settled, setSettled] = useState(false);
  const seen = useRef(new Set<string>());
  const dirMode = useStore((s) => s.dirMode);
  const boxes = useStore((s) => s.boxes);
  const mode = useStore((s) => s.mode);
  const health = useStore((s) => s.health);
  const impact = useStore((s) => s.impact);
  // Online: topics that changed in the change selected on the dashboard (D31) get a dashed ring
  const changed = useStore((s) => (s.mode === "online" ? s.timeline?.changes.find((c) => c.id === s.selected) : undefined));
  const focus = useStore((s) => s.focus);
  const fitTick = useStore((s) => s.fitTick);
  const isTop = useStore((s) => topView(s) === view);

  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setHeight(el.clientHeight || 600));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const positions = useMemo(() => {
    const nbrs = new Map<string, string[]>();
    for (const e of data.edges) {
      nbrs.set(e.source, [...(nbrs.get(e.source) ?? []), e.target]);
      nbrs.set(e.target, [...(nbrs.get(e.target) ?? []), e.source]);
    }
    return layout(
      data.elements.map((el) => ({ id: el.id, level: data.levels[el.id] ?? 0 })),
      (id) => nbrs.get(id) ?? [],
      height,
    );
  }, [data, height]);

  // slide-out: new cards first sit on the root, one frame later they move to their place
  useEffect(() => {
    setSettled(false);
    const frame = requestAnimationFrame(() => setSettled(true));
    const fit = setTimeout(() => {
      data.elements.forEach((el) => seen.current.add(el.id));
      rf.fitView({ padding: 0.14, maxZoom: 1.1, duration: 320 });
    }, SLIDE_MS + 40);
    return () => { cancelAnimationFrame(frame); clearTimeout(fit); };
  }, [data, positions, rf]);

  useEffect(() => {
    if (fitTick && isTop) rf.fitView({ padding: 0.14, maxZoom: 1.1, duration: 320 });
  }, [fitTick, isTop, rf]);

  const live = mode === "online" ? health : {};
  const nodeHealth = (id: string): string | undefined => {
    if (id.startsWith("t:")) return live[id];
    let worst: string | undefined; // a node is as healthy as its worst output on screen
    for (const e of data.edges) {
      const h = e.source === id ? live[e.target] : undefined;
      if (h && (!worst || RANK[h] > RANK[worst])) worst = h;
    }
    return worst;
  };

  const rootPos = positions.get(data.root ?? "") ?? { x: 0, y: 0 };
  const focusId = focus?.view === view ? focus.id : undefined;
  const nodes: CardNode[] = data.elements.map((el) => {
    const level = data.levels[el.id] ?? 0;
    const isRoot = el.id === data.root;
    const p = plusFor(el, level, isRoot, inBox, dirMode);
    const plus = p && {
      ...p,
      open: boxes.some((b) => b.opener?.view === view && b.opener.plus === `${p.dir}|${el.id}`),
    };
    const fresh = !settled && !seen.current.has(el.id) && !isRoot;
    return {
      id: el.id,
      type: "card",
      position: fresh ? rootPos : positions.get(el.id) ?? rootPos,
      draggable: false,
      selectable: false,
      className: fresh ? "fresh" : undefined,
      data: {
        el, view, isRoot, plus, health: nodeHealth(el.id), focused: el.id === focusId,
        impacted: !!impact && (impact.nodes.has(el.id) || impact.topics.has(el.id)),
        changed: !!changed?.affected.some((a) => "t:" + a.topic === el.id),
      },
    };
  });

  const edges: Edge[] = data.edges.map((e: FlowEdge) => {
    const flowing = !!focusId && (e.source === focusId || e.target === focusId);
    const colour = flowing ? "var(--accent)" : "var(--edge)";
    return {
      id: e.id,
      source: e.source,
      target: e.target,
      animated: flowing, // the focused item's data flow moves (D25)
      style: {
        stroke: colour,
        strokeWidth: e.confidence === "live" ? 3.5 : flowing ? 2.4 : 1.5,
        strokeDasharray: e.confidence === "static-convention" && !flowing ? "6 4" : undefined,
      },
      markerEnd: { type: MarkerType.ArrowClosed, color: colour, width: 16, height: 16 },
      data: e as unknown as Record<string, unknown>,
    };
  });

  return (
    <div className={`graph ${isTop ? "active" : "inactive"}`} ref={box}>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        nodesConnectable={false}
        nodesDraggable={false}
        elementsSelectable={false}
        minZoom={0.08}
        maxZoom={2.5}
        proOptions={{ hideAttribution: true }}
        onEdgeMouseEnter={(ev, edge) => showTip(ev, edge.data as unknown as FlowEdge)}
        onEdgeMouseMove={(ev, edge) => showTip(ev, edge.data as unknown as FlowEdge)}
        onEdgeMouseLeave={() => useStore.getState().set({ tip: null })}
        onNodeMouseEnter={(_, node) => useStore.getState().set({ focus: { view, id: node.id } })}
        onPaneClick={() => useStore.getState().set({ focus: null })}
      >
        <Background variant={BackgroundVariant.Dots} gap={22} size={1} color="var(--grid)" />
        {!inBox && <Controls showInteractive={false} position="bottom-right" />}
      </ReactFlow>
    </div>
  );
}

function showTip(ev: React.MouseEvent, e: FlowEdge) {
  const q = e.qos ?? {};
  const lines = [e.confidence, e.evidence];
  if (q.reliability) lines.push(`QoS ${q.reliability} / ${q.durability ?? "?"} / depth ${q.depth ?? "?"}`);
  useStore.getState().set({ tip: { x: ev.clientX, y: ev.clientY, lines } });
}
