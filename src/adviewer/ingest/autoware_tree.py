"""Orchestrate a full static ingest of an Autoware source tree."""
from __future__ import annotations

import subprocess
from pathlib import Path

from adviewer.model import Graph

from .cpp_source import index_endpoints, index_timers
from .diag_graph_yaml import parse_diag_graph_file
from .launch_xml import LaunchContext, parse_launch_file
from .msg_defs import ingest_msg_dir
from .package_xml import PackageInfo, _is_non_runtime, index_packages
from .rates import apply_rates
from .readme_docs import apply_readme_docs
from .release_data import apply_layers, detect_release
from .topic_params import apply_topic_params


def _git_commit(root: Path) -> str:
    try:
        return subprocess.check_output(
            ["git", "-C", str(root), "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return ""

def _layout(packages: dict[str, PackageInfo]) -> str:
    """'install' if packages sit in <prefix>/share/<name>/ (colcon install space), else 'source'."""
    if not packages:
        return ""
    shared = sum(1 for p in packages.values() if p.path.parent.name == "share" and p.path.name == p.name)
    return "install" if shared * 2 > len(packages) else "source"


def _entry_in_packages(entry: str, packages: dict[str, PackageInfo], root: Path) -> Path | None:
    """The entry launch given in install-space form (<pkg>/share/<pkg>/launch/x.launch.xml) or
    package form (<pkg>/launch/x.launch.xml), found in this tree's package of that name: the
    same adviewer.yaml then works for an install space and for a source tree."""
    path = Path(entry)
    if path.is_absolute() and path.resolve().is_relative_to(root):
        path = path.resolve().relative_to(root)
    parts = path.as_posix().split("/")
    for i, name in enumerate(parts):
        if name in packages:
            rest = parts[i + 1:]
            if rest[:2] == ["share", name]:
                rest = rest[2:]
            candidate = packages[name].path.joinpath(*rest)
            if rest and candidate.is_file():
                return candidate
    return None


def _topic_layers(graph: Graph) -> None:
    """A topic belongs to the layer of its publisher (else of its first reader), not to
    whichever node happened to mention it first."""
    decided: dict[str, bool] = {}  # topic -> layer taken from a publisher?
    for e in graph.edges:
        node = graph.nodes.get(e.node)
        if node is None or node.layer.value == "unknown":
            continue
        from_publisher = e.direction.value == "publish"
        if decided.get(e.topic) is True or (e.topic in decided and not from_publisher):
            continue
        graph.topics[e.topic].layer = node.layer
        decided[e.topic] = from_publisher


def _dedupe_params(graph: Graph) -> None:
    """A file included several times indexes its parameters several times; keep one each."""
    seen: set[tuple[str, str, str, str]] = set()
    unique = []
    for prm in graph.params:
        key = (prm.node, prm.name, prm.evidence, prm.value)
        if key not in seen:
            seen.add(key)
            unique.append(prm)
    graph.params = unique


def _ingest_diag_graph(graph: Graph, ctx: LaunchContext, packages: dict[str, PackageInfo]) -> None:
    """Diagnostic graph: the config the aggregator is launched with (F9), else every config.

    The aggregator node's `graph_file` parameter names the top file; its `files:` are
    followed from there. Only when no launched aggregator has a readable graph_file
    (e.g. a tree without launch files for it) are all `units:` YAML files merged, with a
    warning, because alternative configs then overwrite each other.
    """
    aggregators = {
        n.id for n in graph.nodes.values() if n.package == "autoware_diagnostic_graph_aggregator"
    }

    def runtime_config(text: str) -> bool:  # an existing file, not under example/ or test/
        path = Path(text)
        owner = ctx.package_of(path)
        return path.is_file() and not (owner and _is_non_runtime(path, owner.path))

    configs = sorted(
        {
            Path(prm.value)
            for prm in graph.params
            if prm.node in aggregators and prm.name == "graph_file" and runtime_config(prm.value)
        }
    )
    if configs:
        for config in configs:
            parse_diag_graph_file(config, graph, ctx)
        if len(configs) > 1:
            graph.warn(f"diag graph: {len(configs)} aggregator configs launched, merged")
        return
    graph.warn("diag graph: no launched aggregator with a graph_file; all configs merged (F9)")
    for pkg in packages.values():
        for y in sorted(pkg.path.rglob("*.yaml")):
            if _is_non_runtime(y, pkg.path):
                continue
            try:
                head = y.read_text(encoding="utf-8", errors="replace")[:4000]
            except OSError:
                continue
            if "\nunits:" in head or head.startswith("units:"):
                parse_diag_graph_file(y, graph, ctx)


def ingest_autoware_tree(
    root: Path,
    entry_launch: str | None = None,
    source_root: Path | None = None,
    overlays: list[Path] | None = None,
) -> Graph:
    """Parse launch files, message definitions and diag-graph YAML under `root`.

    entry_launch: optional path to a top-level launch file (e.g. autoware_launch/launch/
    autoware.launch.xml) to walk includes from. Without it every *.launch.xml is parsed
    independently (more coverage, less accurate namespaces).
    overlays: further install spaces / workspaces in overlay order (D13); a package found
    in a later root replaces the same package from an earlier one, as colcon overlays do.
    """
    root = Path(root).resolve()
    graph = Graph()
    graph.meta.autoware_root = str(root)
    graph.meta.autoware_commit = _git_commit(root)
    packages = index_packages(root, graph)
    for overlay in overlays or []:
        overlay = Path(overlay).resolve()
        graph.meta.sources.append(f"overlay:{overlay}")
        packages.update(index_packages(overlay, graph))
    graph.meta.layout = _layout(packages)
    source_packages = index_packages(Path(source_root).resolve()) if source_root else None
    release = detect_release(packages)
    if release is not None:
        graph.meta.autoware_release = release.release
    else:
        graph.warn("release not recognised: no data files in adviewer/data match this tree")
    apply_layers(packages, source_packages, release)
    ctx = LaunchContext(packages, root, [Path(o).resolve() for o in overlays or []])
    if release is not None:
        ctx.emulators = [str(e) for e in (release.load("release.yaml") or {}).get("launch_emulators") or []]
    ctx.endpoints = index_endpoints(source_packages or packages)
    if ctx.endpoints:
        graph.meta.sources.append("adviewer.ingest.cpp_source")


    # message definitions
    for pkg in packages.values():
        ingest_msg_dir(pkg.path, pkg.name, graph, root)
    if graph.msg_types:
        graph.meta.sources.append("adviewer.ingest.msg_defs")

    # launch files
    if entry_launch and not Path(entry_launch).is_file():
        found = _entry_in_packages(entry_launch, packages, root)
        if found is None:
            graph.warn(f"entry launch not found: {entry_launch}; every launch file read on its own (less accurate)")
        entry_launch = str(found) if found else None
    if entry_launch:
        graph.meta.entry_launch = ctx.rel(Path(entry_launch).resolve())
        graph.meta.entry_package = ctx.package_name(graph.meta.entry_launch)
        parse_launch_file(Path(entry_launch).resolve(), graph, ctx)
    else:
        # every file once; files already reached through an <include> are not repeated
        for pattern in ("*.launch.xml", "*.launch.yaml", "*.launch.py"):
            for pkg in packages.values():
                for lf in sorted(pkg.path.rglob(pattern)):
                    if lf not in ctx.visited:
                        parse_launch_file(lf, graph, ctx, layer=pkg.layer)
    graph.meta.sources.append("adviewer.ingest.launch_xml")
    graph.meta.sources.append("adviewer.ingest.launch_py")


    _dedupe_params(graph)
    _topic_layers(graph)
    graph.switches = sorted(ctx.switches.values(), key=lambda sw: sw.name)
    apply_topic_params(graph, release)
    apply_rates(graph, index_timers(source_packages or packages))
    apply_readme_docs(graph, source_packages or packages)  # READMEs live in the source tree
    _ingest_diag_graph(graph, ctx, packages)

    graph.meta.warnings = sorted(set(graph.meta.warnings))
    return graph
