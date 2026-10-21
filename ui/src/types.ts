// Shapes of the FastAPI responses (src/adviewer/api/app.py). Only the fields the UI uses.

export type Dir = "back" | "fwd";
export type DirMode = Dir | "both";
export type Kind = "topic" | "node";

export interface Element {
  id: string; // "t:/topic" or "n:/node"
  label: string;
  kind: Kind;
  layer: string;
  msg_type?: string;
  package?: string;
  external?: boolean;
  inputs: number;
  consumers: number;
  level?: number;
}

export interface Qos {
  reliability?: string;
  durability?: string;
  history?: string;
  depth?: string;
  as_written?: string;
}

export interface FlowEdge {
  id: string;
  source: string;
  target: string;
  confidence: string;
  evidence: string;
  qos: Qos;
}

export interface Expansion {
  root: Element;
  direction: Dir;
  nodes: Element[];
  edges: FlowEdge[];
}

export interface Hit {
  kind: string;
  id: string;
  label: string;
  detail: string;
  score: number;
}

export interface Meta {
  autoware_release: string;
  layout: string;
  counts: Record<string, number>;
  has_live: boolean;
  has_target: boolean;
  has_mrm: boolean;
}

export interface Rate {
  hz: number | null;
  kind: string;
  evidence: string;
  note: string;
}

export interface Doc {
  text: string;
  source: string;
  package: string;
  role: string;
}

export interface Endpoint {
  node: string;
  confidence: string;
  evidence: string;
  qos: Qos;
}

export interface TopicDetails {
  id: string;
  msg_type: string;
  schema: { fields: { name: string; type: string; is_array: boolean }[] } | null;
  layer: string;
  qos: Qos;
  rates: Rate[];
  external: boolean;
  publishers: Endpoint[];
  subscribers: Endpoint[];
  declared_in: string[];
  health: string;
  live: Record<string, number | boolean | string[]>;
  about: {
    docs: Doc[];
    message: { type: string; description: string; source: string };
    role: string[];
    if_it_stops: { affected_nodes: number; failed_diagnostics: number; mrm: boolean | null; mrm_behaviour: string };
  } | null;
}

export interface NodeDetails {
  id: string;
  package: string;
  executable: string;
  layer: string;
  source_file: string;
  inputs: { topic: string; confidence: string; evidence: string; qos: Qos }[];
  outputs: { topic: string; confidence: string; evidence: string; qos: Qos }[];
  rates: Rate[];
  docs: Doc[];
  param_count: number;
  params: { name: string; value: string; evidence: string }[];
  diagnostics: string[];
}

export interface Route {
  route: string;
  how: string;
}

export interface ParamEntry {
  name: string;
  value: string;
  kind: string;
  evidence: string;
  package: string;
  via_arg: string;
  via_expr: string;
  nodes: string[];
  routes: Route[];
}

export interface Gate {
  evidence: string;
  tag: string;
  condition: string;
  on: boolean | null;
  target: string;
  nodes: string[];
}

export interface SwitchInfo {
  name: string;
  value: string;
  cli_arg: string;
  cli_chain: string;
  declared: string;
  gates: Gate[];
  nodes: string[];
  node_count: number;
  boolean: boolean;
  command: string;
}

export interface Impact {
  stopped_topics: string[];
  affected_nodes: { id: string; hop: number; via: string }[];
  failed_diagnostics: { path: string; diag_name: string }[];
  note: string;
  mrm?: { modes: Record<string, boolean>; triggered: boolean; behaviour: string; rule: string; evidence: string[] };
}

export interface GateNode {
  path: string;
  type: string;
  diag_name?: string;
  node?: string;
  children: GateNode[];
}

export interface Mrm {
  mode: string;
  unit: string;
  tree: GateNode | null;
  chain: string[];
  rules: Record<string, string>;
}

export interface Check {
  name: string;
  status: "ok" | "warn" | "error" | "info";
  detail: string;
  fix: string;
}

export interface Live {
  enabled: boolean;
  available?: boolean;
  captured_at?: string;
  health?: Record<string, string>;
  report?: { health?: Record<string, number> };
}

export interface Mapping {
  target: string;
  coverage: number;
  required_count: number;
  pairs: { signal: string; topic: string; cost: number; terms: Record<string, number> }[];
  unassigned_required: string[];
  unused_signals: string[];
  readiness: Record<string, { topic: string; readiness: number | null }>;
}

// Live dashboard (D31): /api/timeline
export interface TimelineRoot {
  topic: string;
  kind: "drop" | "stopped" | "health";
  i: number;
  before_hz: number;
  after_hz: number;
  drop: number;
  health_before: string;
  health_after: string;
  relation: "data path" | "new reader" | "unconnected";
  node: string;
  path: string[];
  followers: string[];
}

export interface Change {
  id: string;
  kind: "launch" | "stop" | "unexplained";
  i: number;
  t: string;
  started: string[];
  stopped: string[];
  status: "affected" | "no effect" | "measuring";
  roots: TimelineRoot[];
  affected: Omit<TimelineRoot, "relation" | "node" | "path" | "followers">[];
  added_topics: string[];
  added_bandwidth_Bps: number;
  resources: {
    cpu?: [number, number];
    mem?: [number, number];
    gpu?: [number, number];
    containers?: { name: string; cpu: [number, number] }[];
  };
  evidence: string;
  answer: string;
}

export interface LaneTopic {
  id: string;
  name: string;
  rates: (number | null)[];
  health: string;
  warn: number | null;
  error: number | null;
  changed: boolean;
}

export interface Lane {
  name: string;
  topics: LaneTopic[];
  counts: Record<string, number>;
  changed: number;
}

export interface Timeline {
  enabled: boolean;
  available?: boolean;
  history?: string;
  windows: { t: string; cpu: number | null; mem: number | null; gpu: number | null }[];
  containers: Record<string, (number | null)[]>;
  events: { i: number; t: string; started: string[]; stopped: string[] }[];
  changes: Change[];
  lanes: Lane[];
  summary: { windows: number; from?: string; to?: string; nodes?: number; topics?: number; changes?: number };
  bucket?: number; // probe windows per chart point (long ranges are thinned)
}
