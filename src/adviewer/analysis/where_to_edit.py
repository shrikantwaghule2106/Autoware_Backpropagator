"""Where to edit a parameter, and how to override it in the user's setup (FR-7). Pure.

Routes, most convenient first:
1. launch argument — when the value (or the file holding it) comes from `$(var X)`, or the
   entry is itself a launch argument: `ros2 launch <pkg> <file> X:=…`. An argument set on
   the command line wins unless an including file passes its own value for it.
2. install space (Docker image) — files in the image do not persist edits (D13), so the
   file is replaced in a derived image or bind-mounted. Image paths assume the install
   space was extracted from `/opt/autoware` of the official image.
3. source tree — edit file:line and rebuild the package.
"""
from __future__ import annotations

from pathlib import PurePosixPath

from adviewer.model import Graph, Param, ParamKind

IMAGE_PREFIX = "/opt/autoware"


def _file_and_line(evidence: str) -> tuple[str, str]:
    file, _, line = evidence.rpartition(":")
    return (file, line) if line.isdigit() else (evidence, "")


def _launch_cmd(graph: Graph) -> str:
    if graph.meta.entry_launch and graph.meta.entry_package:
        return f"ros2 launch {graph.meta.entry_package} {PurePosixPath(graph.meta.entry_launch).name}"
    return "ros2 launch <package> <file>"


def override_routes(prm: Param, graph: Graph) -> list[dict[str, str]]:
    """Ways to change this value, each {"route", "how"}."""
    file, line = _file_and_line(prm.evidence)
    at = f"{file}:{line}" if line else file
    routes: list[dict[str, str]] = []
    if prm.kind == ParamKind.launch_arg:
        routes.append({
            "route": "launch argument",
            "how": f"{_launch_cmd(graph)} {prm.name}:=<value>  (default {prm.value!r} at {at})",
        })
    elif prm.via_arg and prm.kind == ParamKind.param_file:
        target = "/path/to/your_copy.yaml" if not prm.via_expr else "<your folder or file>"
        chain = f"; it reaches the node via {prm.via_expr}" if prm.via_expr else ""
        routes.append({
            "route": "launch argument (own parameter file)",
            "how": f"copy {file}, change line {line} ({prm.name}: {prm.value}), then "
                   f"{_launch_cmd(graph)} {prm.via_arg}:={target}{chain}",
        })
    elif prm.via_arg:
        chain = f" (via {prm.via_expr})" if prm.via_expr else ""
        routes.append({
            "route": "launch argument",
            "how": f"{_launch_cmd(graph)} {prm.via_arg}:=<value>  sets {prm.name}{chain}",
        })
    if graph.meta.layout == "install":
        image_path = f"{IMAGE_PREFIX}/{file}"
        routes.append({
            "route": "Docker image (derived image)",
            "how": f"edit a copy of {at}; in your Dockerfile: COPY your_copy {image_path}",
        })
        routes.append({
            "route": "Docker run (bind mount)",
            "how": f"docker run … -v $PWD/your_copy:{image_path}:ro …",
        })
    else:
        build = f"colcon build --packages-select {prm.package}" if prm.package else "colcon build"
        routes.append({"route": "source edit", "how": f"edit {at}, then {build}"})
    return routes


def find_params(graph: Graph, query: str, limit: int = 200) -> list[dict[str, object]]:
    """Parameter entries whose name contains `query` (case-insensitive), with their routes.

    Entries with the same name, value and file:line (one file applied to several nodes)
    are grouped, listing the nodes.
    """
    q = query.lower().strip()
    groups: dict[tuple[str, str, str], dict[str, object]] = {}
    for prm in graph.params:
        if q and q not in prm.name.lower():
            continue
        key = (prm.name, prm.value, prm.evidence)
        group = groups.get(key)
        if group is None:
            group = {
                **prm.model_dump(mode="json"),
                "nodes": [],
                "routes": override_routes(prm, graph),
            }
            groups[key] = group
            if len(groups) > limit:
                break
        if prm.node:
            nodes = group["nodes"]
            assert isinstance(nodes, list)
            nodes.append(prm.node)
    exact = [g for g in groups.values() if str(g["name"]).lower() == q]
    rest = [g for g in groups.values() if str(g["name"]).lower() != q]
    return exact + rest
