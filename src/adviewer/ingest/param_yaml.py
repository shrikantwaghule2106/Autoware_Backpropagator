"""Read ROS 2 parameter YAML files with line numbers (FR-7, F12).

Shape: {<node key>: {ros__parameters: {name: value, nested: {name: value}}}}. Nested keys
are joined with '.', as ROS 2 names them. The node key is `/**` (every node), a node
name, a fully-qualified name, or a pattern with `*`/`**` (rcl's wildcard rules).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass
class ParamLeaf:
    node_key: str  # e.g. "/**" or "/planning/scenario_planning/velocity_smoother"
    name: str  # e.g. "max_vel" or "obstacle_stop.max_velocity"
    value: str  # scalar text, or a JSON list for sequences
    line: int  # 1-based


_CONSTRUCT = yaml.constructor.SafeConstructor()


def _scalar(node: yaml.Node) -> object:
    """A list item typed as YAML would load it (so 1.0 stays a number in the JSON list)."""
    try:
        return _CONSTRUCT.construct_object(node, deep=True)
    except yaml.YAMLError:
        return getattr(node, "value", None)


def _leaves(node: yaml.Node, prefix: str, node_key: str, out: list[ParamLeaf]) -> None:
    if isinstance(node, yaml.MappingNode):
        for key, value in node.value:
            name = f"{prefix}.{key.value}" if prefix else str(key.value)
            _leaves(value, name, node_key, out)
    elif isinstance(node, yaml.SequenceNode):
        items = [_scalar(item) for item in node.value]
        out.append(ParamLeaf(node_key, prefix, json.dumps(items), node.start_mark.line + 1))
    elif isinstance(node, yaml.ScalarNode):
        out.append(ParamLeaf(node_key, prefix, str(node.value), node.start_mark.line + 1))


def read_param_file(path: Path) -> list[ParamLeaf]:
    """Every parameter leaf in the file. Raises yaml.YAMLError / OSError on unreadable files."""
    root = yaml.compose(Path(path).read_text(encoding="utf-8"))
    out: list[ParamLeaf] = []
    if not isinstance(root, yaml.MappingNode):
        return out
    for key, body in root.value:
        if not isinstance(body, yaml.MappingNode):
            continue
        for inner_key, params in body.value:
            if inner_key.value == "ros__parameters":
                _leaves(params, "", str(key.value), out)
    return out


def _pattern(node_key: str) -> re.Pattern[str]:
    """rcl wildcards: '**' = any number of name tokens, '*' = one token."""
    rx = ""
    i = 0
    while i < len(node_key):
        if node_key.startswith("/**", i):
            rx += r"(/[^/]+)*"
            i += 3
        elif node_key[i] == "*":
            rx += r"[^/]*"
            i += 1
        else:
            rx += re.escape(node_key[i])
            i += 1
    return re.compile(rx + r"\Z")


def applies_to(node_key: str, fqn: str) -> bool:
    """True if a YAML node key selects the node with fully-qualified name `fqn`."""
    if node_key in ("/**", "**"):
        return True
    if "*" in node_key:
        key = node_key if node_key.startswith("/") else "/**/" + node_key
        return bool(_pattern(key).match(fqn))
    if node_key.startswith("/"):
        return node_key == fqn
    return fqn.rsplit("/", 1)[-1] == node_key or fqn == "/" + node_key
