"""Online mode: merge a probe dump into the Graph, health per topic, mismatches (FR-9..FR-11),
and degradation between two dumps (FR-10). Pure: dicts and Graphs in, data out.

Health of a topic from its last probe window:
- expected rate = the topic_state_monitor thresholds (warn / error, `kind == "monitor"`);
- healthy   : rate ≥ warn, or no threshold and messages flowing;
- degraded  : error ≤ rate < warn;
- unhealthy : rate < error, or a threshold exists and nothing arrived, or no publisher;
- idle      : no threshold and nothing arrived (event topics such as a route are normal);
- ""        : the topic was not observed (not subscribed / filtered out).
"""
from __future__ import annotations

import math
from typing import Any

from adviewer.ingest.qos import incompatible
from adviewer.model import Confidence, Direction, Edge, Graph, Node, Rate, Topic

STATS = ("count", "rate_hz", "jitter_ms", "max_gap_ms", "bandwidth_Bps", "mean_size_B")
NON_ROS = "(non-ROS DDS participant)"  # DDS endpoints of participants that are not ROS nodes have no node name


def _num(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number if math.isfinite(number) else 0.0


def _endpoints(raw: Any) -> list[dict[str, Any]]:
    out = []
    for e in raw if isinstance(raw, list) else []:
        if isinstance(e, dict):
            node = e.get("node")
            qos = e.get("qos")
            out.append({**e, "node": node if isinstance(node, str) and node else NON_ROS,
                        "type": str(e.get("type") or ""), "qos": qos if isinstance(qos, dict) else {}})
    return out


def normalize_dump(dump: Any) -> dict[str, Any]:
    """A probe dump made safe to read, whoever wrote it (the probe, an older or newer one, a
    hand-edited file): lists are lists, numbers are finite, every endpoint has a node name, one
    entry per topic name."""
    if not isinstance(dump, dict):
        return {"captured_at": "", "window_s": 0.0, "nodes": [], "topics": []}
    topics: list[dict[str, Any]] = []
    seen: set[str] = set()
    raw_topics = dump.get("topics")
    for t in raw_topics if isinstance(raw_topics, list) else []:
        name = t.get("name") if isinstance(t, dict) else None
        if not isinstance(name, str) or not name or name in seen:
            continue
        seen.add(name)
        types = t.get("types")
        topics.append({**t, **{k: _num(t.get(k)) for k in STATS}, "count": int(_num(t.get("count"))),
                       "types": [x for x in types if isinstance(x, str)] if isinstance(types, list) else [],
                       "measured": bool(t.get("measured")),
                       "publishers": _endpoints(t.get("publishers")), "subscribers": _endpoints(t.get("subscribers"))})
    nodes = dump.get("nodes")
    return {**dump, "captured_at": str(dump.get("captured_at") or ""), "window_s": _num(dump.get("window_s")),
            "nodes": sorted({n for n in nodes if isinstance(n, str) and n}) if isinstance(nodes, list) else [],
            "topics": topics}


def _thresholds(topic: Topic) -> tuple[float | None, float | None]:
    for r in topic.rates:
        if r.kind == "monitor":
            error = None
            if "error below " in r.note:
                try:
                    error = float(r.note.split("error below ")[1].split()[0])
                except (ValueError, IndexError):
                    error = None
            return r.hz, error
    return None, None


def health(topic: Topic, live: dict[str, Any]) -> str:
    if not live.get("measured"):
        return ""
    warn, error = _thresholds(topic)
    return health_from(warn, error, float(live.get("rate_hz") or 0.0), int(live.get("count") or 0),
                       bool(live.get("publishers")))


def health_from(warn: float | None, error: float | None, rate: float, count: int, published: bool) -> str:
    """Health of a measured topic from its thresholds and one window (see the module doc)."""
    if not published:
        return "unhealthy" if warn else "idle"
    if warn is None:
        return "healthy" if count > 1 else "idle"
    if count < 2:
        return "unhealthy"
    if rate >= warn:
        return "healthy"
    if error is not None and rate >= error:
        return "degraded"
    return "unhealthy"


def annotate(graph: Graph, dump: dict[str, Any]) -> dict[str, Any]:
    """Merge `dump` into `graph` in place; returns the mismatch report (FR-11)."""
    dump = normalize_dump(dump)
    stamp = str(dump.get("captured_at", ""))
    graph.meta.live_captured_at = stamp
    evidence = f"probe:{stamp}"
    live_topics = {t["name"]: t for t in dump.get("topics", [])}
    report: dict[str, Any] = {"captured_at": stamp, "missing_topics": [], "type_mismatches": [],
                              "qos_incompatible": [], "below_rate": [], "live_only_topics": []}
    for name, lt in live_topics.items():
        topic = graph.topics.get(name)
        if topic is None:
            topic = graph.add_topic(Topic(id=name))
            report["live_only_topics"].append(name)
        topic.live = {k: lt[k] for k in STATS if k in lt}
        topic.live["types"] = lt.get("types", [])
        topic.live["measured"] = bool(lt.get("measured"))
        topic.live["publishers"] = len(lt.get("publishers", []))
        if lt.get("measured"):
            topic.rate_hz = lt.get("rate_hz")
            topic.rates = [r for r in topic.rates if r.kind != "live"]
            topic.rates.append(Rate(hz=lt.get("rate_hz"), kind="live", evidence=evidence,
                                    note=f"{lt.get('count', 0)} messages in {dump.get('window_s', '?')} s"))
        topic.health = health(topic, {**lt, "publishers": lt.get("publishers", [])})
        types = lt.get("types", [])
        if topic.msg_type and types and topic.msg_type not in types:
            report["type_mismatches"].append({"topic": name, "expected": topic.msg_type, "live": types})
        if not topic.msg_type and len(types) == 1:
            topic.msg_type = types[0]
        for kind, direction in (("publishers", Direction.publish), ("subscribers", Direction.subscribe)):
            for ep in lt.get(kind, []):
                node_id = ep["node"]
                graph.nodes.setdefault(node_id, Node(id=node_id))
                old = next((e for e in graph.edges if e.key == (node_id, name, direction)), None)
                was = f"; static: {old.evidence}" if old else ""
                graph.add_edge(Edge(node=node_id, topic=name, direction=direction,
                                    confidence=Confidence.live, evidence=evidence + was,
                                    qos={k: str(v) for k, v in ep.get("qos", {}).items()}))
        for pub in lt.get("publishers", []):
            for sub in lt.get("subscribers", []):
                reasons = incompatible(pub.get("qos", {}), sub.get("qos", {}))
                if reasons:
                    report["qos_incompatible"].append(
                        {"topic": name, "publisher": pub["node"], "subscriber": sub["node"], "reasons": reasons})
        warn, _ = _thresholds(topic)
        if warn and lt.get("measured") and float(lt.get("rate_hz") or 0) < warn:
            report["below_rate"].append({"topic": name, "rate_hz": lt.get("rate_hz"), "expected_hz": warn})
    live_nodes = set(dump.get("nodes", []))
    for name, topic in graph.topics.items():
        if name in live_topics:
            continue
        static_pubs = [e.node for e in graph.publishers(name) if e.confidence != Confidence.live]
        if static_pubs:
            running = [n for n in static_pubs if n in live_nodes]
            report["missing_topics"].append({"topic": name, "static_publishers": static_pubs,
                                             "publisher_running": bool(running)})
            topic.health = "unhealthy" if running or _thresholds(topic)[0] else ""
    report["health"] = {h: sum(1 for t in graph.topics.values() if t.health == h)
                        for h in ("healthy", "degraded", "unhealthy", "idle")}
    return report


def compare(base: dict[str, Any], loaded: dict[str, Any], drop: float = 0.1) -> dict[str, Any]:
    """FR-10: rate drops between a baseline dump and a dump under more load (e.g. cameras on).

    A drop is a relative decrease of more than `drop` on a topic measured in both windows.
    The load change is what the second dump adds: new topics and the bandwidth they carry.
    """
    base, loaded = normalize_dump(base), normalize_dump(loaded)
    a = {t["name"]: t for t in base.get("topics", []) if t.get("measured")}
    b = {t["name"]: t for t in loaded.get("topics", []) if t.get("measured")}
    drops = []
    for name in sorted(set(a) & set(b)):
        ra, rb = float(a[name].get("rate_hz") or 0), float(b[name].get("rate_hz") or 0)
        if ra > 0 and (ra - rb) / ra > drop:
            drops.append({"topic": name, "before_hz": round(ra, 3), "after_hz": round(rb, 3),
                          "drop": round((ra - rb) / ra, 3),
                          "max_gap_ms_after": b[name].get("max_gap_ms")})
    drops.sort(key=lambda d: -d["drop"])
    added = sorted(set(b) - set(a))
    added_bw = sum(float(b[t].get("bandwidth_Bps") or 0) for t in added)
    total_a = sum(float(t.get("bandwidth_Bps") or 0) for t in a.values())
    total_b = sum(float(t.get("bandwidth_Bps") or 0) for t in b.values())
    summary = (f"{len(drops)} topic(s) dropped more than {int(drop * 100)} % while {len(added)} "
               f"topic(s) were added (+{added_bw / 1e6:.2f} MB/s); total "
               f"{total_a / 1e6:.2f} → {total_b / 1e6:.2f} MB/s")
    return {"drops": drops, "added_topics": added, "removed_topics": sorted(set(a) - set(b)),
            "added_bandwidth_Bps": added_bw, "total_bandwidth_Bps": [total_a, total_b],
            "summary": summary}
