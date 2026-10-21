"""Details of one topic or node (FR-5, FR-6). Pure."""
from __future__ import annotations

from typing import Any

from adviewer.model import Direction, Graph

from .impact import _leaf_node, _short, shutdown_impact


def topic_details(graph: Graph, name: str) -> dict[str, Any] | None:
    t = graph.topics.get(name)
    if t is None:
        return None
    schema = graph.msg_types.get(t.msg_type) if t.msg_type else None
    pubs = [e for e in graph.edges if e.topic == name and e.direction == Direction.publish]
    subs = [e for e in graph.edges if e.topic == name and e.direction == Direction.subscribe]

    def ends(edges: list) -> list[dict[str, Any]]:
        return [{"node": e.node, "confidence": e.confidence.value, "evidence": e.evidence,
                 "qos": e.qos} for e in edges]

    return {
        "kind": "topic",
        "id": t.id,
        "msg_type": t.msg_type,
        "schema": schema.model_dump() if schema else None,
        "layer": t.layer.value,
        "qos": t.qos,
        "rate_hz": t.rate_hz,
        "rates": [r.model_dump() for r in t.rates],
        "external": not pubs and bool(subs),
        "publishers": ends(pubs),
        "subscribers": ends(subs),
        "declared_in": sorted({e.evidence.split(": ")[0] for e in pubs + subs}),
    }


def node_details(graph: Graph, name: str) -> dict[str, Any] | None:
    n = graph.nodes.get(name)
    if n is None:
        return None
    edges = [e for e in graph.edges if e.node == name]
    params = [p for p in graph.params if p.node == name]
    diags = [u.path for u in graph.diag_units.values()
             if u.type == "diag" and _short(_leaf_node(u.diag_name)) == _short(name)]
    return {
        "kind": "node",
        "id": n.id,
        "package": n.package,
        "executable": n.executable,
        "namespace": n.namespace,
        "layer": n.layer.value,
        "source_file": n.source_file,
        "inputs": [{"topic": e.topic, "confidence": e.confidence.value, "evidence": e.evidence,
                    "qos": e.qos} for e in edges if e.direction == Direction.subscribe],
        "outputs": [{"topic": e.topic, "confidence": e.confidence.value, "evidence": e.evidence,
                     "qos": e.qos} for e in edges if e.direction == Direction.publish],
        "rates": [r.model_dump() for r in n.rates],
        "docs": [d.model_dump() for d in n.docs],
        "param_count": len(params),
        "params": [p.model_dump(mode="json") for p in params[:200]],
        "diagnostics": diags,
    }


def topic_about(graph: Graph, name: str, mrm_rules: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """A short, newcomer-friendly account of one topic (topic docs).

    `docs` and `message` are Autoware's own words (README rows, .msg header) with their
    source; `role` and `if_it_stops` are computed from the graph — nothing is invented.
    """
    t = graph.topics.get(name)
    if t is None:
        return None
    pubs = sorted({e.node for e in graph.edges if e.topic == name and e.direction == Direction.publish})
    subs = sorted({e.node for e in graph.edges if e.topic == name and e.direction == Direction.subscribe})

    def layers(nodes: list[str]) -> list[str]:
        return sorted({graph.nodes[n].layer.value for n in nodes if n in graph.nodes})

    role: list[str] = []
    if pubs:
        pkgs = sorted({graph.nodes[n].package for n in pubs if n in graph.nodes} - {""})
        role.append(f"Produced by {len(pubs)} node(s) in {', '.join(layers(pubs)) or '?'}"
                    + (f" (package {', '.join(pkgs)})" if pkgs else "") + ".")
    else:
        role.append("Nothing inside Autoware publishes it: a driver, simulator bridge or the vehicle "
                    "interface has to provide it." if subs else "No publisher or subscriber found statically.")
    if subs:
        role.append(f"Used by {len(subs)} node(s) in {', '.join(layers(subs)) or '?'}.")
    for r in t.rates:
        if r.kind == "monitor":
            role.append(f"Watched by a topic monitor ({r.note.split(': ', 1)[-1]}); "
                        "if it is too slow, a diagnostic fails.")
    msg = graph.msg_types.get(t.msg_type) if t.msg_type else None
    about: dict[str, Any] = {
        "docs": [d.model_dump() for d in t.docs],
        "message": {"type": t.msg_type, "description": msg.description if msg else "",
                    "source": msg.source_file if msg else ""},
        "role": role,
    }
    impact = shutdown_impact(graph, [name], [], mrm_rules or None)
    about["if_it_stops"] = {
        "affected_nodes": len(impact["affected_nodes"]),
        "failed_diagnostics": len(impact["failed_diagnostics"]),
        "mrm": impact["mrm"]["triggered"] if "mrm" in impact else None,
        "mrm_behaviour": impact["mrm"]["behaviour"] if "mrm" in impact else "",
    }
    return about
