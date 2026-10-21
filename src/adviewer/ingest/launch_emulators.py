"""Python launch files that build their actions from a YAML file at launch time.

They cannot be read statically (D21: never executed), so the few that matter are
reproduced here, each checked line by line against the release it is enabled for in
`data/autoware-<release>/release.yaml` (`launch_emulators`). An emulator refuses to run
(with a warning) if the file no longer contains the code it reproduces.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from adviewer.model import Graph, Layer, Param, ParamKind

from .launch_xml import LaunchContext, _join_ns, parse_launch_file


def _component_state_monitor(
    path: Path, graph: Graph, ctx: LaunchContext, ns: str, layer: Layer, vars_: dict[str, str]
) -> None:
    """autoware_component_state_monitor/launch/component_state_monitor.launch.py (1.9.0):
    for each row of the YAML `file` whose `mode` list holds the `mode` argument, include
    autoware_topic_state_monitor/launch/load_topic_state_monitor{"" | "_tf"}.launch.xml with
    diag_name = "<module>_topic_status" plus the row's `args` (`_tf` when no topic_type)."""
    rel = ctx.rel(path)
    mode, file = vars_.get("mode"), vars_.get("file")
    if not mode or not file or not Path(file).is_file():
        graph.warn(f"{rel}: emulator needs launch arguments 'mode' and 'file', skipped")
        return
    share = ctx.find_pkg_share("autoware_topic_state_monitor")
    if share is None:
        graph.warn(f"{rel}: package autoware_topic_state_monitor not found, skipped")
        return
    text = Path(file).read_text(encoding="utf-8")
    rows = yaml.safe_load(text) or []
    root = yaml.compose(text)
    lines = [item.start_mark.line + 1 for item in getattr(root, "value", [])]
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or mode not in (row.get("mode") or []):
            continue
        args = row.get("args") or {}
        tf_mode = "" if "topic_type" in args else "_tf"
        include = share / "launch" / f"load_topic_state_monitor{tf_mode}.launch.xml"
        inc_vars = dict(vars_)
        inc_vars["diag_name"] = f"{row.get('module')}_topic_status"
        inc_vars["target_container"] = "component_state_monitor/container"
        for key, value in args.items():
            inc_vars[str(key)] = str(value)  # Python str(): True -> "True", as the original
        parse_launch_file(include, graph, ctx, inc_vars, ns, layer)
        # where these values are really set: the row of the YAML file (FR-7)
        fqn = _join_ns(ns, f"topic_state_monitor_{args.get('node_name_suffix', '')}")
        where = f"{ctx.rel(Path(file))}:{lines[index] if index < len(lines) else ''}"
        owner = ctx.package_of(Path(file))
        for key, value in args.items():
            graph.params.append(Param(node=fqn, name=str(key), value=str(value),
                                      kind=ParamKind.launch_param, evidence=where,
                                      package=owner.name if owner else ""))


_EMULATORS = {
    # name in release.yaml: (package, file name, text the file must contain, function)
    "component_state_monitor": (
        "autoware_component_state_monitor",
        "component_state_monitor.launch.py",
        ("create_topic_monitor_node", "load_topic_state_monitor{tf_mode}.launch.xml"),
        _component_state_monitor,
    ),
}


def emulate(
    path: Path,
    graph: Graph,
    ctx: LaunchContext,
    ns: str,
    layer: Layer,
    vars_: dict[str, str],
    enabled: list[str],
) -> None:
    """Run the emulator for this file, if one is enabled for the release."""
    for name in enabled:
        spec = _EMULATORS.get(name)
        if spec is None:
            continue
        package, file_name, markers, fn = spec
        owner = ctx.package_of(path)
        if path.name != file_name or owner is None or owner.name != package:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if not all(m in text for m in markers):
            graph.warn(f"{ctx.rel(path)}: emulator '{name}' does not match this file, skipped")
            return
        fn(path, graph, ctx, ns, layer, vars_)
        return
