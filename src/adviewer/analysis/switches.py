"""Switches: how to make Autoware lighter or reconfigure it (owner request, 2026-10-16). Pure.

A switch is a launch variable used in if= / unless= conditions. For each: its value, the
nodes it starts, where it is declared, and the command-line override when one exists.
Turning a part off can starve others of inputs — `impact.shutdown_impact` on the switch's
nodes shows what would stop.
"""
from __future__ import annotations

from typing import Any

from adviewer.model import Graph, Switch

from .where_to_edit import _launch_cmd

_BOOL = {"true": "false", "false": "true", "1": "0", "0": "1"}


def switch_nodes(sw: Switch) -> list[str]:
    """Nodes that run because this switch is on (from gates that are on)."""
    return sorted({n for g in sw.gates if g.on for n in g.nodes})


def list_switches(graph: Graph) -> list[dict[str, Any]]:
    """All switches, those that start the most nodes first; on/off switches before selectors."""
    out = []
    for sw in graph.switches:
        nodes = switch_nodes(sw)
        flip = _BOOL.get(sw.value.strip().lower())
        command = f"{_launch_cmd(graph)} {sw.cli_arg}:={flip if flip else '<value>'}" if sw.cli_arg else ""
        out.append({**sw.model_dump(), "nodes": nodes, "node_count": len(nodes), "boolean": flip is not None,
                    "command": command})
    out.sort(key=lambda s: (not s["boolean"], -s["node_count"], s["name"]))
    return out
