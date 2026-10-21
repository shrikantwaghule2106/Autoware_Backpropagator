"""Topic-of-interest drill-down (roadmap Phase 2; base version here)."""
from __future__ import annotations

import networkx as nx

from adviewer.model import Graph

from .metrics import to_networkx


def _bounded(g: nx.DiGraph, start: str, depth: int, reverse: bool) -> set[str]:
    src = g.reverse(copy=False) if reverse else g
    return set(nx.single_source_shortest_path_length(src, start, cutoff=depth).keys())


def drilldown(graph: Graph, topic: str, depth: int = 2) -> dict:
    g = to_networkx(graph)
    t = f"t:{topic}"
    if t not in g:
        return {"error": f"topic not in graph: {topic}"}
    up = _bounded(g, t, depth * 2, reverse=True)  # *2: node+topic hops per logical level
    down = _bounded(g, t, depth * 2, reverse=False)
    keep = up | down
    sub = g.subgraph(keep)
    ext = set(graph.external_inputs())
    topic_obj = graph.topics.get(topic)
    schema = graph.msg_types.get(topic_obj.msg_type) if topic_obj and topic_obj.msg_type else None
    return {
        "topic": topic,
        "msg_type": topic_obj.msg_type if topic_obj else "",
        "schema": schema.model_dump() if schema else None,
        "elements": {
            "nodes": [
                {"id": n, **sub.nodes[n]} for n in sub.nodes
            ],
            "edges": [{"source": u, "target": v, **sub.edges[u, v]} for u, v in sub.edges],
        },
        "upstream_external_inputs": sorted(x[2:] for x in up if x[2:] in ext and x.startswith("t:")),
        "publishers": [e.node for e in graph.publishers(topic)],
        "subscribers": [e.node for e in graph.subscribers(topic)],
    }
