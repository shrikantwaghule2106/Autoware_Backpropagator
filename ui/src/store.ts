// One store for the whole UI (zustand). Views read slices; actions.ts does the fetching.
import { create } from "zustand";
import type { Dir, DirMode, Element, FlowEdge, Hit, Meta, Timeline } from "./types";

export interface ViewData {
  root?: string;
  elements: Element[];
  edges: FlowEdge[];
  levels: Record<string, number>; // element id -> level (0 root, <0 inputs, >0 consumers)
}

/** A box (D25): one level of `eid` in direction `dir`, opened from a "+n" in another view. */
export interface Box {
  key: number;
  eid: string;
  dir: Dir;
  opener?: { view: string; plus: string }; // view "main" or "box-<key>", plus = "<dir>|<element id>"
}

export type DetailsView =
  | "topic" | "node" | "param" | "kit" | "diag" | "switch" | "switches" | "impact" | "mrm"
  | "doctor" | "warnings" | "mapping" | "online" | "changes";

export interface State {
  meta?: Meta;
  theme: "dark" | "light";
  mode: "offline" | "online";
  dirMode: DirMode;
  history: Hit[];
  main: ViewData;
  boxes: Box[];
  nextKey: number;
  details: { view: DetailsView; id: string } | null;
  impact: { nodes: Set<string>; topics: Set<string> } | null;
  health: Record<string, string>;
  liveText: { state: string; text: string } | null;
  focus: { view: string; id: string } | null;
  flash: number; // bumps when a click hits an inactive view
  fitTick: number; // bumps on "Fit": the top view fits itself
  tip: { x: number; y: number; lines: string[] } | null; // edge tooltip
  screen: "graph" | "dashboard"; // Online mode: the graph or the Live dashboard (D31)
  timeline: Timeline | null;
  range: number; // dashboard: how many probe windows
  hoverIdx: number | null; // dashboard: the window under the pointer, shared by every chart
  selected: string | null; // dashboard: the change whose answer is open
  set: (patch: Partial<State>) => void;
  remember: (hit: Hit) => void;
  openBox: (fromView: string, eid: string, dir: Dir, plus?: string) => void;
  closeTop: () => void;
  closeDownTo: (key: number) => void;
  closeAll: () => void;
}

const savedTheme = (): "dark" | "light" => {
  try {
    return localStorage.getItem("adviewer-theme") === "light" ? "light" : "dark";
  } catch {
    return "dark";
  }
};

export const useStore = create<State>((set) => ({
  theme: savedTheme(),
  mode: "offline",
  dirMode: "back",
  history: [],
  main: { elements: [], edges: [], levels: {} },
  boxes: [],
  nextKey: 1,
  details: null,
  impact: null,
  health: {},
  liveText: null,
  focus: null,
  flash: 0,
  fitTick: 0,
  tip: null,
  screen: "graph",
  timeline: null,
  range: 180,
  hoverIdx: null,
  selected: null,
  set: (patch) => set(patch),
  remember: (hit) =>
    set((s) => ({ history: [hit, ...s.history.filter((h) => h.id !== hit.id)].slice(0, 12) })),
  openBox: (fromView, eid, dir, plus) =>
    set((s) => ({
      boxes: [...s.boxes, { key: s.nextKey, eid, dir, opener: plus ? { view: fromView, plus } : undefined }],
      nextKey: s.nextKey + 1,
    })),
  // ✕ / Esc: only the top box closes; the one below becomes active (owner, D25)
  closeTop: () => set((s) => ({ boxes: s.boxes.slice(0, -1) })),
  // "−" on an item: its box and the boxes opened from inside it
  closeDownTo: (key) => set((s) => ({ boxes: s.boxes.filter((b) => b.key < key) })),
  closeAll: () => set({ boxes: [] }),
}));

export const viewKey = (box?: Box) => (box ? `box-${box.key}` : "main");
export const topView = (s: State) => (s.boxes.length ? viewKey(s.boxes[s.boxes.length - 1]) : "main");
