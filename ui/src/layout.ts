// Pure layout and labelling — no React, unit-tested in layout.test.ts.
//
// Every card has the same size, so a column layout cannot overlap by construction (the
// owner's overlap report, 2026-10-16): level 0 = the root, inputs at negative levels to the
// left, consumers at positive levels to the right (D24). A busy level is split into side-by-
// side sub-columns so it fits the window at a readable zoom.
import type { Dir, DirMode, Element } from "./types";

export const CARD_W = 236;
export const CARD_H = 54;
export const HGAP = 116; // between levels: room for the "+n" buttons on both sides
export const SUBGAP = 64; // between sub-columns of one level
export const VGAP = 16;

export interface Placed {
  x: number; // top-left, as React Flow expects
  y: number;
}

/** Rows that fit `viewportH` pixels at ~0.9 zoom (at least 4). */
export function rowsThatFit(viewportH: number): number {
  return Math.max(4, Math.floor((viewportH / 0.9 - 60) / (CARD_H + VGAP)));
}

export function layout(
  items: { id: string; level: number }[],
  neighbours: (id: string) => string[],
  viewportH: number,
): Map<string, Placed> {
  const byLevel = new Map<number, string[]>();
  for (const it of items) byLevel.set(it.level, [...(byLevel.get(it.level) ?? []), it.id]);
  const levelOf = new Map(items.map((it) => [it.id, it.level]));
  const levels = [...byLevel.keys()].sort((a, b) => Math.abs(a) - Math.abs(b) || a - b);
  const rows = rowsThatFit(viewportH);
  const centre = new Map<string, { x: number; y: number }>();
  const farX = new Map<number, number>([[0, 0]]);
  for (const l of levels) {
    const ids = byLevel.get(l)!;
    const sign = Math.sign(l);
    const inner = l - sign;
    // order by the mean height of neighbours one level closer to the root: fewer crossings
    const key = (id: string) => {
      const ys = neighbours(id).filter((n) => levelOf.get(n) === inner).map((n) => centre.get(n)?.y ?? 0);
      return ys.length ? ys.reduce((a, b) => a + b, 0) / ys.length : 0;
    };
    ids.sort((a, b) => (l === 0 ? 0 : key(a) - key(b)) || a.localeCompare(b));
    const k = l === 0 ? 1 : Math.min(4, Math.ceil(ids.length / rows));
    const perCol = Math.ceil(ids.length / k);
    const nearX = l === 0 ? 0 : (farX.get(inner) ?? 0) + sign * (CARD_W + HGAP);
    farX.set(l, nearX + sign * (k - 1) * (CARD_W + SUBGAP));
    for (let j = 0; j < k; j++) {
      const col = ids.slice(j * perCol, (j + 1) * perCol);
      const x = nearX + sign * j * (CARD_W + SUBGAP);
      col.forEach((id, i) => centre.set(id, { x, y: (i - (col.length - 1) / 2) * (CARD_H + VGAP) }));
    }
  }
  return new Map([...centre].map(([id, c]) => [id, { x: c.x - CARD_W / 2, y: c.y - CARD_H / 2 }]));
}

/** Which "+n" an item gets (D25): inputs grow left, consumers right; only when n > 0. */
export function plusFor(
  el: Element, level: number, isRoot: boolean, inBox: boolean, dirMode: DirMode,
): { dir: Dir; count: number } | null {
  if (isRoot) {
    if (inBox) return null; // the box shows the root's level already
    if (dirMode === "back" && el.consumers) return { dir: "fwd", count: el.consumers };
    if (dirMode === "fwd" && el.inputs) return { dir: "back", count: el.inputs };
    return null;
  }
  if (level < 0 && el.inputs) return { dir: "back", count: el.inputs };
  if (level > 0 && el.consumers) return { dir: "fwd", count: el.consumers };
  return null;
}

/** Short card title: a node's namespace + name, a topic's last three segments. */
export function shortLabel(id: string): string {
  const name = id.replace(/^[tn]:/, "");
  const parts = name.split("/").filter(Boolean);
  if (id.startsWith("n:")) return parts.slice(-2).join("/") || name;
  return "/" + parts.slice(-3).join("/");
}

/** "sensor_msgs/msg/PointCloud2" → "PointCloud2". */
export function shortType(type?: string): string {
  return type ? type.split("/").pop() ?? type : "";
}

export const LAYERS: Record<string, string> = {
  sensing: "#5b8def", localization: "#4cc38a", perception: "#f59e42", planning: "#c084fc",
  control: "#f2667a", vehicle: "#b8875e", system: "#2dd4bf", map: "#facc15", common: "#a8a29e",
  api: "#67e8f9", evaluator: "#f0abfc", simulator: "#a3e635", visualization: "#fda4af",
  e2e: "#93c5fd", launch: "#94a3b8", external: "#ffffff", unknown: "#8b98b3",
};
export const HEALTH: Record<string, string> = {
  healthy: "#34d399", degraded: "#fbbf24", unhealthy: "#f87171", idle: "#8b98b3",
};
