"""Parse autoware_diagnostic_graph_aggregator YAML configs into DiagUnit records.

Format (verified in the aggregator's loader, Autoware 1.9.0):
  files: [{path: ...}]              # optional, other files to load
  units:                            # list of units
    - path: /autoware/modes/autonomous
      type: and | short-circuit-and | or | diag | link | remove | warn-to-ok | ok | …
      list: [ {type: link, link: /autoware/...}, {type: diag, node: n, name: s}, ... ]
      node: n, name: s              # type diag: watches /diagnostics status "n: s"
                                    # (src/common/config/loader.cpp:178-181; older
                                    # configs wrote `diag: "n: s"`, still accepted)
      link: /path                   # type link
  - { type: remove, path: /p }      # deletes unit /p after all files are read
Unknown keys are kept in `extra`.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from adviewer.model import DiagUnit, Graph

from .launch_xml import LaunchContext

# Mirrors the aggregator's own rules: autoware_diagnostic_graph_aggregator
# src/common/config/substitutions.cpp (Autoware 1.9.0). Innermost first; unknown = error.
_SUBST = re.compile(r"\$\(([^()]*)\)")


def _as_list(value: Any, what: str, rel: str, graph: Graph) -> list[Any]:
    """Return value if it is a list (None counts as empty); otherwise warn and return []."""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    graph.warn(f"{rel}: {what} should be a list, got {type(value).__name__}")
    return []

def _substitute(text: str, yaml_file: Path, ctx: LaunchContext | None, graph: Graph, rel: str) -> str | None:
    """Resolve $(…) the way the aggregator does. Returns None (after a warning) if it can't."""
    while True:
        m = _SUBST.search(text)
        if m is None:
            return text
        words = m.group(1).split(" ")
        if words == ["dirname"]:
            value = str(yaml_file.parent)
        elif len(words) == 2 and words[0] == "find-pkg-share":
            share = ctx.find_pkg_share(words[1]) if ctx else None
            if share is None:
                graph.warn(f"{rel}: cannot resolve substitution '{m.group(0)}'")
                return None
            value = str(share)
        else:
            graph.warn(f"{rel}: cannot resolve substitution '{m.group(0)}'")
            return None
        text = text[: m.start()] + value + text[m.end():]


def _diag_name(item: dict[str, Any]) -> str:
    """The /diagnostics status name a diag unit watches: node + ": " + name, as the aggregator."""
    if item.get("type") != "diag":
        return ""
    if "name" in item:
        node = str(item.get("node") or "")
        return f"{node}: {item['name']}" if node else str(item["name"])
    return str(item.get("diag", ""))


def _unit_from(item: dict[str, Any], rel: str, graph: Graph, auto_id: list[int]) -> DiagUnit:
    utype = str(item.get("type", "unknown"))
    path = item.get("path")
    if not path:
        if utype == "link":
            path = f"{item.get('link')}#link{auto_id[0]}"
        else:
            path = f"__anon_{utype}_{auto_id[0]}"
        auto_id[0] += 1
    unit = DiagUnit(path=str(path), type=utype, diag_name=_diag_name(item), source_file=rel)
    if utype == "link" and item.get("link"):
        unit.children.append(str(item["link"]))
    for child in _as_list(item.get("list"), f"'list' of unit {path}", rel, graph):
        if not isinstance(child, dict):
            graph.warn(f"{rel}: child unit should be a mapping, got {type(child).__name__}")
            continue
        cu = _unit_from(child, rel, graph, auto_id)

        graph.diag_units.setdefault(cu.path, cu)
        unit.children.append(cu.path)
    for k, v in item.items():
        if k not in ("type", "path", "list", "diag", "link", "node", "name"):
            unit.extra[k] = str(v)
    return unit


def parse_diag_graph_file(
    path: Path,
    graph: Graph,
    ctx: LaunchContext | None = None,
    seen: set[Path] | None = None,
    removals: list[str] | None = None,
) -> None:
    """Read one diag graph config and the files it lists; `remove` edits apply at the end."""
    top = removals is None
    removals = removals if removals is not None else []
    _parse_file(Path(path), graph, ctx, seen if seen is not None else set(), removals)
    if top:
        for gone in removals:
            if graph.diag_units.pop(gone, None) is None:
                graph.warn(f"diag graph: remove of unknown unit '{gone}'")
            for unit in graph.diag_units.values():
                if gone in unit.children:
                    unit.children.remove(gone)


def _parse_file(
    path: Path, graph: Graph, ctx: LaunchContext | None, seen: set[Path], removals: list[str]
) -> None:
    if path in seen:
        return
    seen.add(path)
    rel = ctx.rel(path) if ctx else path.as_posix()
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (yaml.YAMLError, OSError) as exc:
        graph.warn(f"diag yaml parse error {rel}: {exc}")
        return
    if not isinstance(data, dict):
        graph.warn(f"{rel}: top level should be a mapping, got {type(data).__name__}")
        return


    auto_id = [0]
    for item in _as_list(data.get("units"), "'units'", rel, graph):
        if not isinstance(item, dict):
            graph.warn(f"{rel}: unit should be a mapping, got {type(item).__name__}")
            continue
        if item.get("type") == "remove":  # applied after every file is read (loader.cpp:318)
            removals.append(str(item.get("path", "")))
            continue
        unit = _unit_from(item, rel, graph, auto_id)
        graph.diag_units[unit.path] = unit
    for f in _as_list(data.get("files"), "'files'", rel, graph):
        p = f.get("path", "") if isinstance(f, dict) else str(f)
        resolved = _substitute(p, path, ctx, graph, rel)
        if resolved is None:
            continue
        p = resolved
        target = Path(p) if Path(p).is_absolute() else path.parent / p
        if target.exists():
            _parse_file(target, graph, ctx, seen, removals)
        else:
            graph.warn(f"{rel}: diag graph file not found '{p}'")
    if "adviewer.ingest.diag_graph_yaml" not in graph.meta.sources:
        graph.meta.sources.append("adviewer.ingest.diag_graph_yaml")
