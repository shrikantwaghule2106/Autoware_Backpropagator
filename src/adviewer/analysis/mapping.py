"""Target signal → Autoware input assignment, coverage, readiness, SPOF. Pure.

Target descriptor (target.yaml, parsed by the caller), schema v1:
  target: <name>
  provides:                        # what the simulator / hardware bridge publishes
    - name: lidar_top_points
      ros_type: sensor_msgs/msg/PointCloud2   # optional; a mismatch is a hard fail
      type_hint: carla.LidarMeasurement       # free text, shown only
      fields: [x, y, z, intensity]            # compared with the message fields
      rate_hz: 10
      frame: lidar_top
      qos: {reliability: best_effort}         # optional; checked against subscribers
(`signals:` is accepted as an alias of `provides:`.)

Required inputs R = topics Autoware subscribes to with no publisher inside Autoware,
minus debug topics and topics only monitors read. Every score returns its terms (§ notes).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import networkx as nx
import numpy as np
from scipy.optimize import linear_sum_assignment

from adviewer.ingest.qos import incompatible
from adviewer.model import Graph

from .metrics import to_networkx

HARD = 1e6  # "infinite" cost: never assigned
MONITOR_PACKAGES = {"autoware_processing_time_checker", "autoware_topic_state_monitor"}


@dataclass
class TargetSignal:
    name: str
    type_hint: str = ""  # free-form, e.g. "carla.Image"
    ros_type: str = ""  # e.g. sensor_msgs/msg/Image, if the bridge declares it
    fields: list[str] = field(default_factory=list)
    rate_hz: float | None = None
    frame: str = ""
    qos: dict[str, str] = field(default_factory=dict)


@dataclass
class Weights:
    type: float = 1.0
    name: float = 0.2
    rate: float = 0.3
    qos: float = 1.0


def signals_from_descriptor(data: dict[str, Any]) -> list[TargetSignal]:
    items = data.get("provides") or data.get("signals") or []
    out = []
    for s in items:
        if isinstance(s, dict) and s.get("name"):
            out.append(TargetSignal(
                name=str(s["name"]), type_hint=str(s.get("type_hint", "")),
                ros_type=str(s.get("ros_type", "")), fields=[str(f) for f in s.get("fields") or []],
                rate_hz=float(s["rate_hz"]) if s.get("rate_hz") else None, frame=str(s.get("frame", "")),
                qos={str(k): str(v) for k, v in (s.get("qos") or {}).items()}))
    return out


def required_inputs(graph: Graph) -> list[str]:
    """External inputs a bridge must provide (debug and monitor-only topics left out)."""
    out = []
    for t in graph.external_inputs():
        if "/debug/" in t:
            continue
        readers = {graph.nodes[e.node].package for e in graph.subscribers(t) if e.node in graph.nodes}
        if readers and readers <= MONITOR_PACKAGES:
            continue
        out.append(t)
    return out


def _d_name(a: str, b: str) -> float:
    ta = set(a.lower().replace("/", "_").split("_")) - {""}
    tb = set(b.lower().replace("/", "_").split("_")) - {""}
    if not ta or not tb:
        return 1.0
    return 1.0 - len(ta & tb) / len(ta | tb)


def _d_type(sig: TargetSignal, graph: Graph, topic: str) -> float:
    t = graph.topics.get(topic)
    msg_type = t.msg_type if t else ""
    if sig.ros_type and msg_type:
        return 0.0 if sig.ros_type == msg_type else HARD
    msg = graph.msg_types.get(msg_type) if msg_type else None
    if msg is None or not sig.fields:
        return 0.5  # unknown — neutral
    mf = {f.name.lower() for f in msg.fields}
    sf = {f.lower() for f in sig.fields}
    return 1.0 - len(mf & sf) / len(mf | sf)


def _expected_hz(graph: Graph, topic: str) -> tuple[float | None, bool]:
    """(rate, is_minimum): a measured rate is a target; a monitor threshold only a minimum."""
    t = graph.topics.get(topic)
    if t is None:
        return None, False
    if t.rate_hz:
        return t.rate_hz, False
    for r in t.rates:
        if r.hz:
            return r.hz, r.kind == "monitor"
    return None, False


def _d_rate(sig: TargetSignal, graph: Graph, topic: str) -> float:
    want, is_minimum = _expected_hz(graph, topic)
    if not sig.rate_hz or not want:
        return 0.0
    if is_minimum:
        return max(0.0, math.log(want / sig.rate_hz))  # only a slower signal is penalised
    return abs(math.log(sig.rate_hz / want))


def _d_qos(sig: TargetSignal, graph: Graph, topic: str) -> float:
    if not sig.qos:
        return 0.0
    for e in graph.subscribers(topic):
        if e.qos and incompatible(sig.qos, e.qos):
            return HARD
    return 0.0


def assign(
    graph: Graph, signals: list[TargetSignal], required: list[str] | None = None, w: Weights | None = None
) -> dict[str, Any]:
    """§1: Hungarian assignment; pairs carry their cost terms; hard fails are never assigned."""
    w = w or Weights()
    required = required if required is not None else required_inputs(graph)
    if not signals or not required:
        return {"pairs": [], "unassigned_required": required, "unused_signals": [s.name for s in signals],
                "coverage": 0.0, "weights": w.__dict__}
    terms: dict[tuple[int, int], dict[str, float]] = {}
    cost = np.zeros((len(signals), len(required)))
    for i, s in enumerate(signals):
        for j, r in enumerate(required):
            parts = {"type": _d_type(s, graph, r), "name": _d_name(s.name, r),
                     "rate": _d_rate(s, graph, r), "qos": _d_qos(s, graph, r)}
            terms[(i, j)] = parts
            total = w.type * parts["type"] + w.name * parts["name"] + w.rate * parts["rate"] + w.qos * parts["qos"]
            cost[i, j] = min(total, HARD)
    rows, cols = linear_sum_assignment(cost)
    pairs = []
    for i, j in zip(rows, cols):
        if cost[i, j] >= HARD or terms[(i, j)]["type"] > 0.9:  # incompatible or nothing in common
            continue
        r = required[j]
        pairs.append({"signal": signals[i].name, "topic": r, "cost": round(float(cost[i, j]), 4),
                      "terms": {k: round(v, 4) for k, v in terms[(i, j)].items()},
                      "msg_type": graph.topics[r].msg_type if r in graph.topics else "",
                      "consumers": sorted({e.node for e in graph.subscribers(r)})})
    assigned = {p["topic"] for p in pairs}
    used = {p["signal"] for p in pairs}
    return {
        "pairs": pairs,
        "unassigned_required": [r for r in required if r not in assigned],
        "unused_signals": [s.name for s in signals if s.name not in used],
        "coverage": round(len(assigned) / len(required), 3),
        "weights": w.__dict__,
    }


def readiness(graph: Graph, assigned: set[str], required: list[str], targets: dict[str, str]) -> dict[str, Any]:
    """§3: per subsystem, the weighted share of its required inputs that are assigned.
    Weight of an input = 1 + its betweenness on paths from R to the subsystem's topic."""
    g = to_networkx(graph)
    out: dict[str, Any] = {}
    for name, topic in targets.items():
        target = f"t:{topic}"
        if target not in g:
            out[name] = {"topic": topic, "readiness": None, "inputs": [], "note": "topic not in graph"}
            continue
        anc = nx.ancestors(g, target)
        inputs = [r for r in required if f"t:{r}" in anc]
        if not inputs:
            out[name] = {"topic": topic, "readiness": None, "inputs": [], "note": "no external inputs upstream"}
            continue
        sub = g.subgraph(anc | {target})
        betw = nx.betweenness_centrality_subset(sub, sources=[f"t:{r}" for r in inputs],
                                                targets=[target], normalized=True)
        weights = {r: 1.0 + betw.get(f"t:{r}", 0.0) for r in inputs}
        score = sum(wt for r, wt in weights.items() if r in assigned) / sum(weights.values())
        out[name] = {"topic": topic, "readiness": round(score, 3),
                     "inputs": [{"topic": r, "weight": round(weights[r], 3), "assigned": r in assigned}
                                for r in sorted(inputs, key=lambda r: -weights[r])]}
    return out


def single_points_of_failure(graph: Graph, topic: str) -> list[str]:
    """§2: articulation points of the ancestor subgraph of `topic` (undirected projection)."""
    g = to_networkx(graph)
    target = f"t:{topic}"
    if target not in g:
        return []
    sub = g.subgraph(nx.ancestors(g, target) | {target}).to_undirected()
    if sub.number_of_nodes() < 3:
        return []
    return sorted(p for p in nx.articulation_points(sub) if p != target)


def mapping_report(graph: Graph, descriptor: dict[str, Any], targets: dict[str, str],
                   w: Weights | None = None) -> dict[str, Any]:
    signals = signals_from_descriptor(descriptor)
    required = required_inputs(graph)
    result = assign(graph, signals, required, w)
    assigned = {p["topic"] for p in result["pairs"]}
    result["target"] = descriptor.get("target", "")
    result["required_count"] = len(required)
    result["readiness"] = readiness(graph, assigned, required, targets)
    result["spof"] = {name: single_points_of_failure(graph, t) for name, t in targets.items()}
    return result


def to_markdown(report: dict[str, Any], graph: Graph) -> str:
    """Checklist export: every required input, assigned or not, with what reads it."""
    lines = [f"# Bridge checklist — {report.get('target') or 'target'}", "",
             f"Coverage: {report['coverage'] * 100:.0f} % of {report.get('required_count', 0)} required inputs.", ""]
    for name, r in report.get("readiness", {}).items():
        value = "n/a" if r["readiness"] is None else f"{r['readiness'] * 100:.0f} %"
        lines.append(f"- {name} ({r['topic']}): {value} ready")
    lines += ["", "| ✓ | Autoware input | Type | Signal | Read by |", "|---|---|---|---|---|"]
    by_topic = {p["topic"]: p for p in report["pairs"]}
    for topic in sorted(set(report["unassigned_required"]) | set(by_topic)):
        p = by_topic.get(topic)
        readers = ", ".join(sorted({e.node for e in graph.subscribers(topic)}))
        msg = graph.topics[topic].msg_type if topic in graph.topics else ""
        lines.append(f"| {'✓' if p else '☐'} | `{topic}` | {msg} | {p['signal'] if p else ''} | {readers} |")
    if report.get("unused_signals"):
        lines += ["", "Signals not used: " + ", ".join(report["unused_signals"])]
    return "\n".join(lines) + "\n"
