// Thin client for the read-only FastAPI (src/adviewer/api/app.py).
import type {
  Check, Expansion, Hit, Impact, Live, Mapping, Meta, Mrm, NodeDetails, ParamEntry, SwitchInfo, Timeline, TopicDetails,
} from "./types";

async function get<T>(path: string, params?: Record<string, string | number>): Promise<T> {
  const query = params ? "?" + new URLSearchParams(Object.entries(params).map(([k, v]) => [k, String(v)])) : "";
  const r = await fetch(path + query);
  if (!r.ok) {
    const body = await r.json().catch(() => ({}) as { detail?: string });
    throw new Error((body as { detail?: string }).detail || r.statusText);
  }
  return (await r.json()) as T;
}

export const api = {
  meta: () => get<Meta>("/api/meta"),
  warnings: () => get<string[]>("/api/warnings"),
  search: (q: string) => get<Hit[]>("/api/search", { q }),
  expand: (id: string, dir: string) => get<Expansion>("/api/expand", { id, dir }),
  trace: (id: string, dir: string, depth: number) => get<Expansion>("/api/trace", { id, dir, depth }),
  topic: (id: string) => get<TopicDetails>("/api/details", { id }),
  node: (id: string) => get<NodeDetails>("/api/details", { id }),
  param: (name: string) => get<{ entries: ParamEntry[] }>("/api/details", { id: "p:" + name }),
  kit: (name: string) => get<{ packages: string[]; nodes: string[] }>("/api/details", { id: "kit:" + name }),
  diag: (path: string) =>
    get<{ path: string; type: string; diag_name: string; source_file: string; children: string[] }>(
      "/api/details", { id: "d:" + path }),
  switch: (name: string) => get<SwitchInfo>("/api/details", { id: "s:" + name }),
  switches: () => get<SwitchInfo[]>("/api/switches"),
  impact: (id: string) => get<Impact>("/api/impact", { id }),
  mrm: () => get<Mrm>("/api/mrm"),
  doctor: () => get<{ status: string; checks: Check[] }>("/api/doctor"),
  live: () => get<Live>("/api/live"),
  mapping: () => get<Mapping>("/api/mapping"),
  timeline: (last: number) => get<Timeline>("/api/timeline", { last }),
};
