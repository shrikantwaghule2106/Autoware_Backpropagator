"""Search, one-level expansion and depth-k trace over the Graph (FR-2, FR-3, FR-5). Pure.

Element ids: "t:<topic>", "n:<node>". Edges always point in data-flow direction:
node → topic (publish), topic → node (subscribe).
"""
from __future__ import annotations

from typing import Any

from adviewer.model import Direction, Edge, Graph

KIND_ORDER = {"topic": 0, "node": 1, "sensor_kit": 2, "switch": 3, "diag": 4, "param": 5}


# --- elements -------------------------------------------------------------------------
Degrees = dict[str, tuple[int, int]]


def degrees(graph: Graph) -> Degrees:
    """Element id -> (number of inputs, number of consumers), one hop each way.

    The UI shows a "+n" only where n > 0, so a + always opens something (D25).
    """
    count: dict[str, list[int]] = {}
    for e in graph.edges:
        node, topic = f"n:{e.node}", f"t:{e.topic}"
        if e.direction == Direction.publish:  # node -> topic
            count.setdefault(node, [0, 0])[1] += 1
            count.setdefault(topic, [0, 0])[0] += 1
        else:  # topic -> node
            count.setdefault(topic, [0, 0])[1] += 1
            count.setdefault(node, [0, 0])[0] += 1
    return {k: (v[0], v[1]) for k, v in count.items()}


def element(
    graph: Graph, eid: str, external: set[str] | None = None, deg: Degrees | None = None
) -> dict[str, Any] | None:
    """UI data for one topic or node, with its input / consumer counts."""
    kind, _, name = eid.partition(":")
    ins, outs = (deg if deg is not None else degrees(graph)).get(eid, (0, 0))
    if kind == "t" and name in graph.topics:
        t = graph.topics[name]
        ext = external if external is not None else set(graph.external_inputs())
        return {"id": eid, "label": name, "kind": "topic", "layer": t.layer.value,
                "msg_type": t.msg_type, "external": name in ext, "inputs": ins, "consumers": outs}
    if kind == "n" and name in graph.nodes:
        n = graph.nodes[name]
        return {"id": eid, "label": name, "kind": "node", "layer": n.layer.value,
                "package": n.package, "inputs": ins, "consumers": outs}
    return None


def edge_data(e: Edge) -> dict[str, Any]:
    src, dst = (f"n:{e.node}", f"t:{e.topic}") if e.direction == Direction.publish else (
        f"t:{e.topic}", f"n:{e.node}")
    return {"id": f"{src}->{dst}", "source": src, "target": dst,
            "confidence": e.confidence.value, "evidence": e.evidence, "qos": e.qos}


def neighbours(graph: Graph, eid: str, direction: str) -> list[Edge]:
    """Edges one hop from `eid`. direction "back" = what it needs, "fwd" = who uses it."""
    kind, _, name = eid.partition(":")
    if kind == "t":
        want = Direction.publish if direction == "back" else Direction.subscribe
        return [e for e in graph.edges if e.topic == name and e.direction == want]
    want = Direction.subscribe if direction == "back" else Direction.publish
    return [e for e in graph.edges if e.node == name and e.direction == want]


def expand(graph: Graph, eid: str, direction: str) -> dict[str, Any]:
    """One level (D24/D25): the item's direct inputs (back) or consumers (fwd), as elements.
    Each element carries `inputs` / `consumers` counts for its own "+n"."""
    ext = set(graph.external_inputs())
    deg = degrees(graph)
    edges = neighbours(graph, eid, direction)
    others = [f"n:{e.node}" if eid.startswith("t:") else f"t:{e.topic}" for e in edges]
    nodes = [d for d in (element(graph, o, ext, deg) for o in dict.fromkeys(others)) if d]
    for d in nodes:  # kept for API compatibility: can it grow in the same direction?
        d["more"] = (d["inputs"] if direction == "back" else d["consumers"]) > 0
    return {"root": element(graph, eid, ext, deg), "direction": direction,
            "nodes": nodes, "edges": [edge_data(e) for e in edges]}


def trace(graph: Graph, eid: str, direction: str, depth: int) -> dict[str, Any]:
    """All elements up to `depth` hops (a hop = topic→node or node→topic)."""
    ext = set(graph.external_inputs())
    deg = degrees(graph)
    level = {eid: 0}
    frontier = [eid]
    edges: dict[str, dict[str, Any]] = {}
    for hop in range(1, depth + 1):
        nxt = []
        for cur in frontier:
            for e in neighbours(graph, cur, direction):
                other = f"n:{e.node}" if cur.startswith("t:") else f"t:{e.topic}"
                data = edge_data(e)
                edges[data["id"]] = data
                if other not in level:
                    level[other] = hop
                    nxt.append(other)
        frontier = nxt
    nodes = []
    for other, hop in level.items():
        d = element(graph, other, ext, deg)
        if d:
            d["level"] = -hop if direction == "back" else hop
            nodes.append(d)
    return {"root": element(graph, eid, ext, deg), "direction": direction, "nodes": nodes,
            "edges": list(edges.values())}


# --- search ------------------------------------------------------------------------------
def kit_nodes(graph: Graph, packages: list[str]) -> list[str]:
    """Nodes declared in launch files of the given packages (a sensor kit)."""
    prefixes = tuple(f"{p}/" for p in packages)
    return [n.id for n in graph.nodes.values()
            if any(f"/{p}" in f"/{n.source_file}" for p in prefixes)]


def _expand_query(q: str, aliases: dict[str, list[str]]) -> list[str]:
    words = [q]
    for key, synonyms in aliases.items():
        if len(q) >= 2 and (key.startswith(q) or q == key):
            words.extend(s.lower() for s in synonyms)
    return list(dict.fromkeys(words))


def _score(text: str, q: str, words: list[str]) -> int:
    low = text.lower()
    if low == q:
        return 100
    last = low.rstrip("/").rsplit("/", 1)[-1]
    if last == q:
        return 90
    segments = low.split("/")
    if q in segments:  # a whole segment: /sensing/lidar/… for "lidar"
        return 88
    if last.startswith(q):
        return 85
    if any(seg.startswith(q) for seg in low.replace("_", "/").split("/")):
        return 80
    if q in low:
        return 60
    if any(w in low for w in words[1:]):
        return 40
    return 0


def search(
    graph: Graph,
    query: str,
    aliases: dict[str, list[str]] | None = None,
    kits: dict[str, list[str]] | None = None,
    limit: int = 40,
) -> list[dict[str, Any]]:
    """Partial, alias-aware matching over topics, nodes, sensor kits, diag units, parameters."""
    q = query.lower().strip()
    if not q:
        return []
    words = _expand_query(q, aliases or {})
    hits: list[dict[str, Any]] = []

    def add(kind: str, hid: str, label: str, detail: str, text: str) -> None:
        score = _score(text, q, words)
        if score and kind in ("switch", "param"):  # secondary: below topics/nodes of the same match
            score -= 15
        if score:
            hits.append({"kind": kind, "id": hid, "label": label, "detail": detail, "score": score})

    for t in graph.topics.values():
        add("topic", f"t:{t.id}", t.id, t.msg_type or t.layer.value, t.id)
    for n in graph.nodes.values():
        add("node", f"n:{n.id}", n.id, n.package, n.id)
    for name, packages in (kits or {}).items():
        members = kit_nodes(graph, packages)
        if members:
            add("sensor_kit", f"kit:{name}", name, f"{len(members)} nodes", name)
    for sw in graph.switches:
        on = len({n for g in sw.gates if g.on for n in g.nodes})
        add("switch", f"s:{sw.name}", sw.name, f"= {sw.value} · {on} node(s)", sw.name)
    for u in graph.diag_units.values():
        if not u.path.startswith("__anon") and "#" not in u.path:
            add("diag", f"d:{u.path}", u.path, u.diag_name or u.type, u.path)
    seen_params: set[str] = set()
    for p in graph.params:
        if p.name not in seen_params:
            seen_params.add(p.name)
            add("param", f"p:{p.name}", p.name, f"{p.value} ({p.kind.value})", p.name)
    hits.sort(key=lambda h: (-h["score"], KIND_ORDER[h["kind"]], len(h["label"])))
    return hits[:limit]
