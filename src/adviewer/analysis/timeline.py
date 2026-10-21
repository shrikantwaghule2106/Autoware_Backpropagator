"""Live timeline (D31): launch events, a change log and a one-level answer per change. Pure.

Input: the Graph (thresholds from the static analysis, wiring from static + live edges) and the
recorded probe windows (`window_record`, one per probe window, oldest first). A window carries
the live wiring (who publishes / reads each topic) when it changed since the window before;
the analysis uses all of it, so a node that has stopped is still known as the source of what
it published. Output: plain data for the API and the dashboard.

Method
- Launch events: the running-node list changes between two windows. Nodes that appear in the
  same window form one event (a launch group). A node missing from a single window is treated
  as discovery jitter, not as a stop and a start.
- Changes per topic: at window k the rate falls by more than `drop` against the median of up
  to `span` windows before k, or the topic stops, or its health gets worse than at any point
  in those windows, and this holds for `persist` windows in a row. Rate drops and stops count
  only for a topic that was steady before (at least 3 windows, received in each, rates within
  ±50 % of their median); a topic that publishes on change (a speed limit, a route) counts as stopped
  only when its publishers disappear.
- A change belongs to the latest launch event at most `tolerance` windows before it; the rest
  are grouped as changes "without a launch event" at the window where they began.
- Root topics: changed topics with no changed topic upstream of them (through publishing
  nodes, up to `depth` hops). Autoware has feedback loops (lidar → localization → velocity →
  lidar concatenation), so topics in one loop are upstream of each other; such a group gets
  one root, the topic earliest in the pipeline (sensing → localization → perception →
  planning → control), then the largest drop. The other changed topics are followers.
- Relation of a root to the nodes that started (or stopped), from the graph wiring, along a
  path of at most `near` topics (a longer path through a loop is not presented as a cause):
    data path   - such a node publishes the root or a topic just upstream of it;
    new reader  - such a node subscribes the root (or a topic feeding it): one more delivery;
    unconnected - neither; they share only the machine, so the answer quotes the resource
                  change (CPU, memory, GPU, containers) over the same windows.
  This reports coincidence in time plus the graph connection, not a proven cause.
"""
from __future__ import annotations

import math
from collections import deque
from collections.abc import Sequence
from statistics import median
from typing import Any

from adviewer.analysis.live import _num, _thresholds, health_from, normalize_dump
from adviewer.model import Direction, Graph, Topic

RANK = {"": 0, "idle": 0, "healthy": 0, "degraded": 1, "unhealthy": 2}
LANE_ORDER = ["lidar", "camera", "radar", "imu", "gnss", "localization", "perception", "planning",
              "control", "vehicle", "system", "api", "map"]


def window_record(dump: dict[str, Any], resources: dict[str, Any] | None = None) -> dict[str, Any]:
    """One history line from a probe dump: running nodes, measured topics, machine load."""
    dump = normalize_dump(dump)
    topics: dict[str, list[float]] = {}
    for t in dump.get("topics", []):
        if not t.get("measured"):
            continue
        topics[t["name"]] = [int(t.get("count") or 0), round(float(t.get("rate_hz") or 0), 3),
                             round(float(t.get("jitter_ms") or 0), 3), round(float(t.get("max_gap_ms") or 0), 1),
                             round(float(t.get("bandwidth_Bps") or 0)), len(t.get("publishers", []))]
    return {"t": str(dump.get("captured_at", "")), "window_s": float(dump.get("window_s") or 0),
            "nodes": sorted(dump.get("nodes", [])), "topics": topics, "res": clean_res(resources)}


def clean_res(res: Any) -> dict[str, Any]:
    """Machine load of one window with only finite numbers (host %, containers' CPU and memory)."""
    def number(v: Any) -> bool:
        return isinstance(v, (int, float)) and not isinstance(v, bool) and _num(v) == v

    if not isinstance(res, dict):
        return {}
    out: dict[str, Any] = {k: float(res[k]) for k in ("cpu", "mem", "gpu", "gpu_mem") if number(res.get(k))}
    rows = res.get("containers")
    if isinstance(rows, dict):
        clean = {str(c): {"cpu": float(v["cpu"]), "mem_MB": _num(v.get("mem_MB"))}
                 for c, v in rows.items() if isinstance(v, dict) and number(v.get("cpu"))}
        if clean:
            out["containers"] = clean
    return out


def wiring_of(dump: dict[str, Any]) -> dict[str, dict[str, list[str]]]:
    """Live wiring of a probe dump: publishing and reading nodes per topic."""
    dump = normalize_dump(dump)
    return {kind: {t["name"]: sorted({ep["node"] for ep in t.get(key, []) if ep.get("node")})
                   for t in dump.get("topics", []) if t.get(key)}
            for kind, key in (("pub", "publishers"), ("sub", "subscribers"))}


def lane_of(topic: str) -> str:
    parts = topic.strip("/").split("/")
    if parts[0] == "sensing" and len(parts) > 2:
        return parts[1]
    return parts[0] or "other"


def _clock(stamp: str) -> str:
    return stamp[11:19] if len(stamp) >= 19 else stamp


class _Wiring:
    """Who publishes and who reads each topic (static and live edges)."""

    def __init__(self, graph: Graph, windows: Sequence[dict[str, Any]] = ()):
        self.pubs: dict[str, set[str]] = {}
        self.subs: dict[str, set[str]] = {}
        self.reads: dict[str, set[str]] = {}  # node -> topics it subscribes
        for e in graph.edges:
            self._add(e.node, e.topic, e.direction == Direction.publish)
        for w in windows:  # recorded live wiring, including nodes that have stopped since
            for kind, publish in (("pub", True), ("sub", False)):
                for topic, nodes in (w.get("wiring") or {}).get(kind, {}).items():
                    for n in nodes:
                        self._add(n, topic, publish)

    def _add(self, node: str, topic: str, publish: bool) -> None:
        if publish:
            self.pubs.setdefault(topic, set()).add(node)
        else:
            self.subs.setdefault(topic, set()).add(node)
            self.reads.setdefault(node, set()).add(topic)

    def upstream(self, topic: str, depth: int) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
        """Topics and nodes upstream of `topic`, each with the topic path down to `topic`."""
        topics: dict[str, list[str]] = {}
        nodes: dict[str, list[str]] = {}
        queue = deque([(topic, [topic], 0)])
        while queue:
            t, path, d = queue.popleft()
            for n in sorted(self.pubs.get(t, ())):
                if n in nodes:
                    continue
                nodes[n] = path
                if d >= depth:
                    continue
                for up in sorted(self.reads.get(n, ())):
                    if up != topic and up not in topics:
                        topics[up] = [up, *path]
                        queue.append((up, [up, *path], d + 1))
        return topics, nodes


def _med(values: list[float]) -> float | None:
    vals = [v for v in values if v is not None]
    return float(median(vals)) if vals else None


def _smoothed_nodes(windows: list[dict[str, Any]]) -> list[set[str]]:
    """Running nodes per window; a node missing from one window between two is kept."""
    sets = [set(w.get("nodes", [])) for w in windows]
    out = [set(s) for s in sets]
    for k in range(1, len(sets) - 1):
        out[k] |= sets[k - 1] & sets[k + 1]
    return out


def _events(windows: list[dict[str, Any]], persist: int) -> list[dict[str, Any]]:
    nodes = _smoothed_nodes(windows)
    events = []
    for k in range(1, len(windows)):
        started = sorted(nodes[k] - nodes[k - 1])
        # a stop needs the node gone for the next window too (unless k is the last window)
        stopped = sorted(n for n in nodes[k - 1] - nodes[k]
                         if k + 1 >= len(windows) or n not in nodes[k + 1])
        if started or stopped:
            events.append({"i": k, "t": windows[k]["t"], "started": started, "stopped": stopped,
                           "pending": len(windows) - 1 - k < persist})
    return events


class _Series:
    def __init__(self, graph: Graph, windows: list[dict[str, Any]]):
        per_window = [w.get("topics", {}) for w in windows]
        names = sorted({n for topics in per_window for n in topics})
        self.names = names
        self.rate: dict[str, list[float | None]] = {}
        self.rank: dict[str, list[int]] = {}
        self.health: dict[str, list[str]] = {}
        self.bw: dict[str, list[float | None]] = {}
        self.count: dict[str, list[int]] = {}
        self.pubs: dict[str, list[int]] = {}
        for name in names:
            warn, error = _thresholds(graph.topics.get(name) or Topic(id=name))  # once per topic
            rates: list[float | None] = []
            hs: list[str] = []
            bws: list[float | None] = []
            counts: list[int] = []
            npubs: list[int] = []
            for topics in per_window:
                v = topics.get(name)
                if v is None:
                    rates.append(None)
                    bws.append(None)
                    hs.append("")
                    counts.append(0)
                    npubs.append(0)
                    continue
                try:  # the recorder writes clean numbers: the fast path
                    count, rate, bw, pubs = int(v[0]), float(v[1]), float(v[4]), int(v[5])
                    if not (math.isfinite(rate) and math.isfinite(bw)):
                        raise ValueError
                except (TypeError, ValueError, IndexError, KeyError):  # a damaged or older line
                    c, rate, _jit, _gap, bw, p = [_num(x) for x in ((list(v) if isinstance(v, list) else []) + [0] * 6)[:6]]
                    count, pubs = int(c), int(p)
                rates.append(rate)
                bws.append(bw)
                counts.append(count)
                npubs.append(pubs)
                hs.append(health_from(warn, error, rate, count, pubs > 0) if warn is not None
                          else ("healthy" if count > 1 else "idle") if pubs > 0 else "idle")
            self.rate[name] = rates
            self.health[name] = hs
            self.rank[name] = [RANK.get(h, 0) for h in hs]
            self.bw[name] = bws
            self.count[name] = counts
            self.pubs[name] = npubs


def _steady(rates: list[float | None], counts: list[int]) -> bool:
    """At least 3 windows, received in each (2+ messages), rates within ±50 % of their median."""
    if len(rates) < 3 or any(r is None or r <= 0 for r in rates) or any(c < 2 for c in counts):
        return False
    mid = float(median([r for r in rates if r is not None]))
    return all(abs(float(r) - mid) <= 0.5 * mid for r in rates if r is not None)


def _onsets(s: _Series, name: str, span: int, persist: int, drop: float) -> list[dict[str, Any]]:
    rates, ranks, hs = s.rate[name], s.rank[name], s.health[name]
    counts, pubs = s.count[name], s.pubs[name]
    present = [r for r in rates if r is not None]
    if (len(present) == len(rates) and min(present, default=0) > (1 - drop) * max(present, default=0) > 0
            and len(set(ranks)) == 1 and min(pubs, default=0) > 0):
        return []  # steady all along: no window can drop, stop or get worse (most topics; keeps big histories fast)
    n = len(rates)
    out = []
    k = 1
    while k <= n - persist:
        lo = max(0, k - span)
        before = _med([r for r in rates[lo:k] if r is not None]) or 0.0
        after = [r if r is not None else 0.0 for r in rates[k:k + persist]]
        gone = max(pubs[lo:k]) > 0 and all(p == 0 for p in pubs[k:k + persist])  # its publishers left
        steady = before > 0 and _steady(rates[lo:k], counts[lo:k])
        stopped = all(a == 0 for a in after) and (steady or gone)
        dropped = steady and all((before - a) / before > drop for a in after)
        worse = before > 0 and all(ranks[j] > max(ranks[lo:k]) for j in range(k, k + persist))
        if stopped or dropped or worse:
            after_hz = float(median(after))
            out.append({"topic": name, "i": k,
                        "kind": "stopped" if stopped else "drop" if dropped else "health",
                        "before_hz": round(before, 2), "after_hz": round(after_hz, 2),
                        "drop": round((before - after_hz) / before, 3) if before > 0 else 1.0,
                        "health_before": next((h for h in reversed(hs[lo:k]) if h), ""),
                        "health_after": hs[k + persist - 1]})
            k += max(span, persist)  # the next change is judged against the new level
        else:
            k += 1
    return out


def _resources(windows: list[dict[str, Any]], lo: int, mid: int, hi: int) -> dict[str, Any]:
    """Median machine load before [lo, mid) and after (mid, hi]: CPU / memory / GPU and the
    containers whose CPU changed most."""
    def res(w: dict[str, Any]) -> dict[str, Any]:
        value = w.get("res")
        return value if isinstance(value, dict) else {}

    def number(value: Any) -> bool:
        return isinstance(value, (int, float)) and not isinstance(value, bool)

    def med(key: str, part: list[dict[str, Any]]) -> float | None:
        return _med([_num(res(w).get(key)) for w in part if number(res(w).get(key))])

    before, after = windows[lo:mid], windows[mid + 1:hi + 1]
    out: dict[str, Any] = {}
    for key in ("cpu", "mem", "gpu"):
        b, a = med(key, before), med(key, after)
        if b is not None and a is not None:
            out[key] = [round(b, 1), round(a, 1)]
    def cpu(w: dict[str, Any], c: str) -> float | None:
        row = (res(w).get("containers") or {}).get(c) if isinstance(res(w).get("containers"), dict) else None
        value = row.get("cpu") if isinstance(row, dict) else None
        return _num(value) if number(value) else None

    names = {c for w in before + after if isinstance(res(w).get("containers"), dict) for c in res(w)["containers"]}
    rows = []
    for c in names:
        b = _med([v for w in before if (v := cpu(w, c)) is not None]) or 0.0
        a = _med([v for w in after if (v := cpu(w, c)) is not None]) or 0.0
        rows.append({"name": c, "cpu": [round(b, 1), round(a, 1)]})
    rows.sort(key=lambda r: -(r["cpu"][1] - r["cpu"][0]))
    if rows:
        out["containers"] = rows[:3]
    return out


def _relation(w: _Wiring, root: str, nodes: list[str], near: int) -> dict[str, Any]:
    """`near` = the longest topic path (root included) that still counts as connected."""
    up_topics, up_nodes = w.upstream(root, near)
    for n in nodes:
        if n in up_nodes and len(up_nodes[n]) <= near:
            return {"relation": "data path", "node": n, "path": up_nodes[n]}
    for n in nodes:
        if n in w.subs.get(root, ()):
            return {"relation": "new reader", "node": n, "path": [root]}
    for n in nodes:
        hit = next((t for t in sorted(up_topics) if n in w.subs.get(t, ()) and len(up_topics[t]) <= near), None)
        if hit:
            return {"relation": "new reader", "node": n, "path": up_topics[hit]}
    return {"relation": "unconnected", "node": "", "path": []}


def _pct(x: float) -> str:
    return f"{x * 100:.0f} %"


def _resource_text(res: dict[str, Any]) -> str:
    parts = [f"{label} {res[k][0]:.0f} → {res[k][1]:.0f} %" for k, label in
             (("cpu", "CPU"), ("gpu", "GPU"), ("mem", "memory")) if k in res]
    top = (res.get("containers") or [None])[0]
    if top and top["cpu"][1] - top["cpu"][0] >= 5:
        parts.append(f"container {top['name']} CPU {top['cpu'][0]:.0f} → {top['cpu'][1]:.0f} %")
    return ", ".join(parts)


def _who(nodes: list[str]) -> str:
    """Started/stopped nodes, grouped by namespace: single nodes by name, groups by count."""
    groups: dict[str, list[str]] = {}
    for n in nodes:
        groups.setdefault(n.rsplit("/", 1)[0] or "/", []).append(n)
    parts = [ns_nodes[0] if len(ns_nodes) == 1 else f"{len(ns_nodes)} in {ns}"
             for ns, ns_nodes in sorted(groups.items(), key=lambda kv: (len(kv[1]), kv[0]))]
    return ", ".join(parts[:3]) + (f" and {len(parts) - 3} more group(s)" if len(parts) > 3 else "")


def _level(res: dict[str, Any]) -> bool:
    """True when no machine figure moved by 5 points or more."""
    moves = [abs(res[k][1] - res[k][0]) for k in ("cpu", "gpu", "mem") if k in res]
    moves += [abs(c["cpu"][1] - c["cpu"][0]) / 4 for c in res.get("containers", [])]  # container CPU is per core
    return bool(moves) and max(moves) < 5


def _answer(change: dict[str, Any]) -> str:
    who = change["started"] or change["stopped"]
    verb = "started" if change["started"] else "stopped"
    clock = _clock(change["t"])
    res = _resource_text(change["resources"])
    if change["status"] == "measuring":
        return f"{len(who)} node(s) {verb} at {clock} ({_who(who)}). Measuring the effect (needs a few more probe windows)."
    if not change["roots"]:
        return f"{len(who)} node(s) {verb} at {clock} ({_who(who)}). No measured topic changed."
    r = change["roots"][0]
    what = ("stopped" if r["kind"] == "stopped" else
            f"fell {r['before_hz']} → {r['after_hz']} Hz (−{_pct(r['drop'])})" if r["kind"] == "drop" else
            f"turned {r['health_after']} ({r['before_hz']} → {r['after_hz']} Hz)")
    if not who:
        text = f"At {clock}, {r['topic']} {what} with no node started or stopped just before."
        text += (" The machine load stayed level, so look at its inputs in the graph." if _level(change["resources"])
                 else f" Machine: {res}." if res else " Look at its inputs in the graph.")
    else:
        text = f"{len(who)} node(s) {verb} at {clock} ({_who(who)}). Then {r['topic']} {what}."
        if r["relation"] == "data path":
            via = " → ".join(r["path"])
            text += (f" {r['node']} publishes into it ({via}): look at that node first." if verb == "started"
                     else f" Its source {r['node']} stopped ({via}).")
        elif r["relation"] == "new reader":
            text += (f" {r['node']} now reads {r['path'][0]}, which adds one more delivery of every message."
                     if verb == "started" else f" {r['node']}, which read {r['path'][0]}, stopped.")
        else:
            text += f" No topic connects it to the nodes that {verb}; they share only the machine" + (f": {res}." if res else ".")
    followers = sum(len(x["followers"]) for x in change["roots"])
    more = len(change["roots"]) - 1
    if followers:
        text += f" {followers} downstream topic(s) followed."
    if more:
        text += f" {more} other root topic(s) changed too."
    return text


def _roots(wiring: _Wiring, affected: dict[str, dict[str, Any]], depth: int) -> list[str]:
    """Changed topics with no changed topic upstream, one per feedback loop (see module doc)."""
    up = {t: set(wiring.upstream(t, depth)[0]) for t in affected}
    free = [t for t in affected if not any(u in up[t] and t not in up[u] for u in affected if u != t)]
    order = {n: i for i, n in enumerate(LANE_ORDER)}
    rank = (lambda t: (order.get(lane_of(t), len(order)), -affected[t]["drop"], t))
    roots: list[str] = []
    for t in sorted(free, key=rank):
        if not any(r in up[t] and t in up[r] for r in roots):  # same loop as a chosen root
            roots.append(t)
    return roots or sorted(affected, key=rank)[:1]


def _bucket(values: list[Any], size: int, pick: Any) -> list[Any]:
    out = []
    for i in range(0, len(values), size):
        part = [v for v in values[i:i + size] if v is not None]
        out.append(pick(part) if part else None)
    return out


def downsample(tl: dict[str, Any], points: int) -> dict[str, Any]:
    """At most `points` per chart: rates keep each bucket's lowest value (a drop stays visible),
    machine load its highest; event and change positions move to their bucket."""
    n = len(tl.get("windows", []))
    if points <= 0 or n <= points:
        return {**tl, "bucket": 1}
    size = -(-n // points)
    windows = tl["windows"]
    out = {**tl, "bucket": size}
    out["windows"] = [{"t": windows[i]["t"], **{k: (max(vals) if (vals := [w[k] for w in windows[i:i + size] if w.get(k) is not None]) else None)
                                                  for k in ("cpu", "mem", "gpu")}} for i in range(0, n, size)]
    out["containers"] = {c: _bucket(v, size, max) for c, v in tl.get("containers", {}).items()}
    out["lanes"] = [{**lane, "topics": [{**t, "rates": _bucket(t["rates"], size, min)} for t in lane["topics"]]}
                    for lane in tl.get("lanes", [])]
    out["events"] = [{**e, "i": e["i"] // size} for e in tl.get("events", [])]
    out["changes"] = [{**c, "i": c["i"] // size} for c in tl.get("changes", [])]
    return out


def timeline(graph: Graph, windows: list[dict[str, Any]], span: int = 6, persist: int = 2,
             drop: float = 0.1, tolerance: int = 2, depth: int = 8, near: int = 3) -> dict[str, Any]:
    """Events, change log with one-level answers, lanes and machine load for `windows`."""
    windows = [{**w, "t": str(w.get("t") or ""), "res": clean_res(w.get("res")),  # skip or repair damaged lines
                "nodes": [n for n in w["nodes"] if isinstance(n, str)] if isinstance(w.get("nodes"), list) else []}
               for w in windows if isinstance(w, dict) and isinstance(w.get("topics"), dict)]
    if not windows:
        return {"windows": [], "events": [], "changes": [], "lanes": [], "summary": {"windows": 0}}
    s = _Series(graph, windows)
    wiring = _Wiring(graph, windows)
    events = _events(windows, persist)
    onsets = [o for name in s.names for o in _onsets(s, name, span, persist, drop)]

    groups: dict[str, dict[str, Any]] = {}
    for e in events:
        groups[f"e{e['i']}"] = {**e, "kind": "launch" if e["started"] else "stop", "affected": []}
    for o in onsets:
        owner = next((e for e in reversed(events) if e["i"] <= o["i"] <= e["i"] + tolerance), None)
        key = f"e{owner['i']}" if owner else f"u{o['i']}"
        if key not in groups:
            groups[key] = {"i": o["i"], "t": windows[o["i"]]["t"], "started": [], "stopped": [],
                           "pending": False, "kind": "unexplained", "affected": []}
        groups[key]["affected"].append(o)

    changes = []
    for key, g in groups.items():
        i = g["i"]
        prev = max((e["i"] for e in events if e["i"] < i), default=0)
        nxt = min((e["i"] for e in events if e["i"] > i), default=len(windows))
        lo, hi = max(prev, i - span), min(nxt - 1, i + span, len(windows) - 1)
        affected = {o["topic"]: o for o in g["affected"]}
        roots = []
        for name in _roots(wiring, affected, depth) if affected else []:
            followers = sorted(t for t in affected if t != name and name in wiring.upstream(t, depth)[0])
            roots.append({**affected[name], **_relation(wiring, name, g["started"] or g["stopped"], near),
                          "followers": followers})
        added = [n for n in s.names if (first := next((k for k, r in enumerate(s.rate[n]) if r is not None), None))
                 is not None and i <= first <= i + tolerance and first > 0] if g["kind"] != "unexplained" else []
        added_bw = sum(_med([b for b in s.bw[n][i:hi + 1] if b is not None]) or 0.0 for n in added)
        change = {"id": key, "kind": g["kind"], "i": i, "t": g["t"], "started": g["started"],
                  "stopped": g["stopped"],
                  "status": "measuring" if g["pending"] and not affected else "affected" if affected else "no effect",
                  "roots": roots, "affected": sorted(affected.values(), key=lambda o: -o["drop"]),
                  "added_topics": added, "added_bandwidth_Bps": round(added_bw),
                  "resources": _resources(windows, lo, i, hi),
                  "evidence": f"probe windows {windows[lo]['t']} … {windows[hi]['t']}"}
        change["answer"] = _answer(change)
        changes.append(change)
    changes.sort(key=lambda c: -c["i"])

    changed = {o["topic"] for o in onsets}
    lanes: dict[str, list[dict[str, Any]]] = {}
    for name in s.names:
        topic = graph.topics.get(name) or Topic(id=name)
        warn, error = _thresholds(topic)
        last = next((h for h in reversed(s.health[name]) if h), "")
        lanes.setdefault(lane_of(name), []).append({
            "id": f"t:{name}", "name": name, "rates": [None if r is None else round(r, 2) for r in s.rate[name]],
            "health": last, "warn": warn, "error": error, "changed": name in changed})
    order = {n: i for i, n in enumerate(LANE_ORDER)}
    lane_list = []
    for lane, topics in sorted(lanes.items(), key=lambda kv: (order.get(kv[0], len(order)), kv[0])):
        topics.sort(key=lambda t: (not t["changed"], -RANK.get(t["health"], 0), t["warn"] is None, t["name"]))
        counts = {h: sum(1 for t in topics if t["health"] == h) for h in ("healthy", "degraded", "unhealthy", "idle")}
        lane_list.append({"name": lane, "topics": topics, "counts": counts,
                          "changed": sum(1 for t in topics if t["changed"])})

    containers: dict[str, list[float | None]] = {}
    for k, w in enumerate(windows):
        for c, v in (w.get("res", {}).get("containers") or {}).items():
            containers.setdefault(c, [None] * len(windows))[k] = v.get("cpu")
    top = sorted(containers, key=lambda c: -max((v or 0) for v in containers[c]))[:5]
    return {
        "windows": [{"t": w["t"], "cpu": w.get("res", {}).get("cpu"), "mem": w.get("res", {}).get("mem"),
                     "gpu": w.get("res", {}).get("gpu")} for w in windows],
        "containers": {c: containers[c] for c in top},
        "events": [{k: e[k] for k in ("i", "t", "started", "stopped")} for e in events],
        "changes": changes,
        "lanes": lane_list,
        "summary": {"windows": len(windows), "from": windows[0]["t"], "to": windows[-1]["t"],
                    "nodes": len(windows[-1].get("nodes", [])), "topics": len(s.names),
                    "changes": sum(1 for c in changes if c["status"] == "affected")},
    }
