// What the user's clicks do: fetch from the API and update the store.
import { api } from "./api";
import { type DetailsView, topView, useStore } from "./store";
import type { Dir, Element, FlowEdge, Hit } from "./types";

const DETAILS_OF: Record<string, DetailsView> = {
  t: "topic", n: "node", p: "param", kit: "kit", d: "diag", s: "switch",
};
const KIND_OF: Record<string, string> = {
  t: "topic", n: "node", p: "param", kit: "sensor_kit", d: "diag", s: "switch",
};

/** Open any id ("t:/x", "n:/y", "p:max_vel", "kit:…", "d:/path", "s:launch_x") like a search hit. */
export function go(id: string): Promise<void> {
  const prefix = id.split(":")[0];
  return run({ kind: KIND_OF[prefix] ?? "topic", id, label: id.replace(/^[a-z]+:/, ""), detail: "", score: 0 });
}

export function showDetails(id: string) {
  const prefix = id.split(":")[0];
  const view = DETAILS_OF[prefix];
  if (view) useStore.getState().set({ details: { view, id: id.replace(/^[a-z]+:/, "") } });
}

export async function run(hit: Hit): Promise<void> {
  const st = useStore.getState();
  st.remember(hit);
  if (hit.kind === "mrm") return showMrm();
  if (hit.kind !== "topic" && hit.kind !== "node") return showDetails(hit.id);
  if (st.screen !== "graph") st.set({ screen: "graph" }); // a dashboard link opens the graph (deep dive)
  // a topic or node becomes the new root, drawn from the entry block leftwards (D24, D25)
  st.closeAll();
  const dirMode = st.dirMode;
  const back = await api.expand(hit.id, "back");
  const elements: Element[] = [back.root];
  const edges: FlowEdge[] = [];
  const levels: Record<string, number> = { [hit.id]: 0 };
  if (dirMode !== "fwd") {
    back.nodes.forEach((n) => { elements.push(n); levels[n.id] = -1; });
    edges.push(...back.edges);
  }
  if (dirMode !== "back") {
    const fwd = await api.expand(hit.id, "fwd");
    fwd.nodes.forEach((n) => { if (!(n.id in levels)) { elements.push(n); levels[n.id] = 1; } });
    edges.push(...fwd.edges);
  }
  useStore.getState().set({ main: { root: hit.id, elements, edges, levels }, focus: { view: "main", id: hit.id } });
  showDetails(hit.id);
}

/** MRM: the main view shows only the MRM chain; every +n opens its full level in a box. */
export async function showMrm(): Promise<void> {
  const st = useStore.getState();
  st.set({ details: { view: "mrm", id: "" } });
  const g = await api.mrm();
  if (!g.chain.length) return;
  st.closeAll();
  const chain = g.chain.map((t) => "t:" + t);
  const res = await api.trace(chain[chain.length - 1], "back", 2 * chain.length);
  const keep = new Set(chain);
  for (const e of res.edges) {
    if (keep.has(e.target) && e.source.startsWith("n:") && res.edges.some((f) => f.target === e.source && keep.has(f.source))) {
      keep.add(e.source);
    }
  }
  for (const e of res.edges) if (e.target === chain[0] && e.source.startsWith("n:")) keep.add(e.source);
  const elements = [res.root, ...res.nodes.filter((n) => keep.has(n.id) && n.id !== res.root.id)];
  const levels: Record<string, number> = { [res.root.id]: 0 };
  for (const n of elements) if (n.id !== res.root.id) levels[n.id] = n.level ?? -1;
  useStore.getState().set({
    main: { root: res.root.id, elements, edges: res.edges.filter((e) => keep.has(e.source) && keep.has(e.target)), levels },
  });
}

/** A "+n" (or "−") on an item in view `view`. */
export function plusClicked(view: string, eid: string, dir: Dir) {
  const st = useStore.getState();
  const plus = `${dir}|${eid}`;
  const open = st.boxes.find((b) => b.opener?.view === view && b.opener.plus === plus);
  if (open) return st.closeDownTo(open.key); // "−"
  if (view !== topView(st)) return st.set({ flash: st.flash + 1 });
  st.openBox(view, eid, dir, plus);
  showDetails(eid);
}

/** A click on an item: details, unless a box lies above this view. */
export function itemClicked(view: string, eid: string) {
  const st = useStore.getState();
  if (view !== topView(st)) return st.set({ flash: st.flash + 1 });
  st.set({ focus: { view, id: eid } });
  showDetails(eid);
}

/** Open a box from the details panel ("Inputs in a box"). */
export function openBoxFor(eid: string, dir: Dir) {
  const st = useStore.getState();
  st.openBox(topView(st), eid, dir);
}
