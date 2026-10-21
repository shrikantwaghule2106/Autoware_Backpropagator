"""Edges from topic-valued parameters (F12), e.g. the lidar concatenator's `input_topics`.

Rules come from the release's `topic_params.yaml` (package + parameter → direction, each
checked in C++). Evidence = the parameter's file:line plus the rule's C++ line.
"""
from __future__ import annotations

import json

from adviewer.model import Confidence, Direction, Edge, Graph, Topic

from .launch_xml import _resolve_topic
from .release_data import ReleaseData


def _names(value: str) -> list[str]:
    if value.startswith("["):
        try:
            items = json.loads(value)
        except json.JSONDecodeError:
            return []
        return [str(i) for i in items if isinstance(i, str)]
    return [value]


def apply_topic_params(graph: Graph, release: ReleaseData | None) -> int:
    """Add the edges; returns how many were added."""
    data = release.load("topic_params.yaml") if release else None
    rules = {
        (str(r["package"]), str(r["param"])): r for r in (data or {}).get("rules", []) or []
    }
    if not rules:
        return 0
    added = 0
    for prm in graph.params:
        node = graph.nodes.get(prm.node)
        rule = rules.get((node.package, prm.name)) if node else None
        if node is None or rule is None:
            continue
        direction = Direction(rule["direction"])
        for name in _names(prm.value):
            if not name or name.startswith("$("):
                continue
            topic = graph.add_topic(
                Topic(id=_resolve_topic(name, node.id, node.namespace), layer=node.layer)
            )
            graph.add_edge(
                Edge(
                    node=node.id,
                    topic=topic.id,
                    direction=direction,
                    confidence=Confidence.static_source,
                    evidence=f"{prm.evidence}: param {prm.name}; {rule['evidence']}",
                )
            )
            added += 1
    return added
