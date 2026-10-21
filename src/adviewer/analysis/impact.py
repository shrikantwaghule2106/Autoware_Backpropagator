"""Shutdown impact (FR-4) and the diagnostics that gate MRM. Pure.

Impact model — stated in every result, because a static graph cannot know how a node
reacts to a missing input:
- a seed topic is dead; a seed node (or every node of a sensor kit) is dead;
- a node that subscribes to a dead topic is *affected*; its outputs count as dead
  (conservative: some nodes keep publishing with a missing optional input);
- a topic is dead only when every one of its publishers is dead or affected, so shared
  topics such as /diagnostics stay alive;
- a diagnostic leaf `node: name` fails when its node is dead or affected (a stopped node's
  status goes stale; a topic_state_monitor whose topic stops reports an error);
- failures propagate up the diagnostic graph with the aggregator's logics: and /
  short-circuit-and fail if any child fails, or fails if all children fail, link and
  warn-to-* pass the failure through, ok/const never fail;
- a mode is available only if its unit is OK; MRM follows the release's mrm.yaml rules.
"""
from __future__ import annotations

from collections import deque
from typing import Any

import yaml

from adviewer.model import Direction, Graph

MODEL_NOTE = (
    "Static estimate: affected = subscribes to a stopped topic; its outputs are assumed to "
    "stop too (conservative). A topic stops only when all its publishers stop."
)


def _short(name: str) -> str:
    return name.rstrip("/").rsplit("/", 1)[-1]


def _leaf_node(diag_name: str) -> str:
    node, sep, _ = diag_name.partition(": ")
    return node if sep else ""


def failed_units(graph: Graph, bad_nodes: set[str]) -> tuple[list[str], set[str]]:
    """(failing diag leaves, all failing unit paths) when the nodes in `bad_nodes` fail."""
    shorts = {_short(n) for n in bad_nodes} | bad_nodes
    failed: set[str] = set()
    leaves = []
    for u in graph.diag_units.values():
        node = _leaf_node(u.diag_name)
        if u.type == "diag" and node and (node in shorts or _short(node) in shorts):
            failed.add(u.path)
            leaves.append(u.path)
    changed = True
    while changed:  # fixed point upwards
        changed = False
        for u in graph.diag_units.values():
            if u.path in failed or not u.children:
                continue
            kids = [c in failed for c in u.children]
            if u.type == "or":
                fails = all(kids)
            elif u.type in ("ok", "stale", "warn", "error", "diag"):
                fails = False
            else:  # and, short-circuit-and, link, warn-to-ok, warn-to-error, unknown
                fails = any(kids)
            if fails:
                failed.add(u.path)
                changed = True
    return leaves, failed


def mode_units(graph: Graph, rules: dict[str, Any]) -> dict[str, str]:
    """Mode name -> unit path, from the aggregator's command_mode_mappings parameter."""
    names = {int(k): str(v) for k, v in (rules.get("mode_names") or {}).items()}
    aggregators = {n.id for n in graph.nodes.values() if n.package == rules.get("aggregator_package")}
    modes: dict[str, str] = {}
    for p in graph.params:
        if p.node in aggregators and p.name == rules.get("mode_mappings_param"):
            try:
                items = yaml.safe_load(p.value) or []
            except yaml.YAMLError:
                continue
            for item in items:
                entry = yaml.safe_load(item) if isinstance(item, str) else item
                if isinstance(entry, dict) and "mode" in entry and "path" in entry:
                    modes[names.get(int(entry["mode"]), str(entry["mode"]))] = str(entry["path"])
    return modes


def _handler_flag(graph: Graph, rules: dict[str, Any], name: str) -> bool | None:
    handlers = {n.id for n in graph.nodes.values() if n.package == rules.get("handler_package")}
    for p in graph.params:
        if p.node in handlers and p.name == name:
            return p.value.strip().lower() == "true"
    return None


def mrm_outcome(graph: Graph, failed: set[str], rules: dict[str, Any]) -> dict[str, Any]:
    modes = mode_units(graph, rules)
    available = {m: path not in failed for m, path in modes.items() if path in graph.diag_units}
    trigger = rules.get("trigger_mode", "autonomous")
    triggered = available.get(trigger) is False
    behaviour = ""
    if triggered:
        if available.get("pull_over") and _handler_flag(graph, rules, "use_pull_over"):
            behaviour = "pull_over"
        elif available.get("comfortable_stop") and _handler_flag(graph, rules, "use_comfortable_stop"):
            behaviour = "comfortable_stop"
        else:
            behaviour = "emergency_stop"
    return {
        "modes": available,
        "triggered": triggered,
        "behaviour": behaviour,
        "rule": rules.get("trigger_rule", ""),
        "evidence": [rules.get("availability_evidence", ""), rules.get("trigger_evidence", ""),
                     rules.get("behaviour_evidence", "")],
    }


def shutdown_impact(
    graph: Graph,
    seed_topics: list[str],
    seed_nodes: list[str],
    rules: dict[str, Any] | None = None,
) -> dict[str, Any]:
    pubs: dict[str, set[str]] = {}
    subs: dict[str, set[str]] = {}
    outs: dict[str, set[str]] = {}
    for e in graph.edges:
        if e.direction == Direction.publish:
            pubs.setdefault(e.topic, set()).add(e.node)
            outs.setdefault(e.node, set()).add(e.topic)
        else:
            subs.setdefault(e.topic, set()).add(e.node)
    down: dict[str, tuple[int, str]] = {n: (0, "stopped") for n in seed_nodes}
    dead: dict[str, int] = {t: 0 for t in seed_topics}
    queue = deque(dead)
    for n in seed_nodes:
        for t in outs.get(n, ()):
            if t not in dead and pubs.get(t, set()) <= set(down):
                dead[t] = 0
                queue.append(t)
    while queue:
        topic = queue.popleft()
        hop = dead[topic]
        for node in subs.get(topic, ()):
            if node in down:
                continue
            down[node] = (hop + 1, topic)
            for out in outs.get(node, ()):
                if out not in dead and pubs.get(out, set()) <= set(down):
                    dead[out] = hop + 1
                    queue.append(out)
    leaves, failed = failed_units(graph, set(down))
    named = sorted(p for p in failed if not p.startswith("__anon") and "#" not in p)
    result: dict[str, Any] = {
        "seeds": {"topics": seed_topics, "nodes": seed_nodes},
        "stopped_topics": sorted(dead, key=lambda t: (dead[t], t)),
        "affected_nodes": [
            {"id": n, "hop": h, "via": via} for n, (h, via) in sorted(down.items(), key=lambda kv: (kv[1][0], kv[0]))
        ],
        "failed_diagnostics": [
            {"path": p, "diag_name": graph.diag_units[p].diag_name} for p in sorted(leaves)
        ],
        "failed_units": named,
        "note": MODEL_NOTE,
    }
    if rules:
        result["mrm"] = mrm_outcome(graph, failed, rules)
    return result


def _gate_tree(graph: Graph, path: str, seen: set[str]) -> dict[str, Any]:
    u = graph.diag_units.get(path)
    if u is None:
        return {"path": path, "type": "missing", "children": []}
    if path in seen:
        return {"path": path, "type": u.type, "children": [], "repeat": True}
    seen = seen | {path}
    if u.type == "link" and u.children:  # show the linked unit in place of the link
        return _gate_tree(graph, u.children[0], seen)
    node = _leaf_node(u.diag_name)
    match = [n.id for n in graph.nodes.values() if node and _short(n.id) == _short(node)]
    return {
        "path": path, "type": u.type, "diag_name": u.diag_name, "node": match[0] if match else "",
        "source_file": u.source_file,
        "children": [_gate_tree(graph, c, seen) for c in u.children],
    }


def mrm_gates(graph: Graph, rules: dict[str, Any]) -> dict[str, Any]:
    """The diagnostic tree behind the MRM trigger mode (acceptance row 'MRM', FR-4)."""
    modes = mode_units(graph, rules)
    trigger = rules.get("trigger_mode", "autonomous")
    path = modes.get(trigger, "")
    return {
        "mode": trigger,
        "unit": path,
        "tree": _gate_tree(graph, path, set()) if path else None,
        "modes": modes,
        "chain": rules.get("chain", []),
        "rules": {k: rules.get(k, "") for k in (
            "availability_rule", "availability_evidence", "trigger_rule", "trigger_evidence",
            "behaviour_rule", "behaviour_evidence")},
    }
