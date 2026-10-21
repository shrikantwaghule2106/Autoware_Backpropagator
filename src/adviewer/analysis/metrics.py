"""Graph metrics: single points of failure and criticality."""
from __future__ import annotations

import networkx as nx

from adviewer.model import Direction, Graph


def to_networkx(graph: Graph) -> nx.DiGraph:
    """node --publish--> topic --subscribe--> node. Node ids prefixed to avoid collisions."""
    g: nx.DiGraph = nx.DiGraph()
    for n in graph.nodes.values():
        g.add_node(f"n:{n.id}", kind="node", layer=n.layer.value, package=n.package)
    for t in graph.topics.values():
        g.add_node(f"t:{t.id}", kind="topic", layer=t.layer.value, msg_type=t.msg_type)
    for e in graph.edges:
        if e.direction == Direction.publish:
            g.add_edge(f"n:{e.node}", f"t:{e.topic}", confidence=e.confidence.value)
        else:
            g.add_edge(f"t:{e.topic}", f"n:{e.node}", confidence=e.confidence.value)
    return g


def criticality(graph: Graph, topic_of_interest: str) -> dict:
    """Articulation points, betweenness (restricted to paths external-inputs → t*) and
    min edge cut for the ancestor subgraph of a topic of interest."""
    g = to_networkx(graph)
    target = f"t:{topic_of_interest}"
    if target not in g:
        return {"error": f"topic not in graph: {topic_of_interest}"}
    anc = nx.ancestors(g, target) | {target}
    sub = g.subgraph(anc).copy()
    sources = [f"t:{t}" for t in graph.external_inputs() if f"t:{t}" in sub]
    arts = sorted(nx.articulation_points(sub.to_undirected())) if sub.number_of_nodes() > 2 else []
    betw = (
        nx.betweenness_centrality_subset(sub, sources=sources, targets=[target], normalized=True)
        if sources
        else {}
    )
    cut: list[tuple[str, str]] = []
    if sources:
        s = "__super_source__"
        aug = sub.copy()
        for src in sources:
            aug.add_edge(s, src)
        try:
            cut = sorted(nx.minimum_edge_cut(aug, s, target))
        except nx.NetworkXError:
            cut = []
    return {
        "topic": topic_of_interest,
        "ancestor_count": len(anc) - 1,
        "external_inputs": [s[2:] for s in sources],
        "articulation_points": [a for a in arts if a != target],
        "betweenness": {k: round(v, 4) for k, v in sorted(betw.items(), key=lambda kv: -kv[1]) if v > 0},
        "min_edge_cut": [[u, v] for u, v in cut if u != "__super_source__"],
        "min_cut_size": len([1 for u, _ in cut if u != "__super_source__"]),
    }
