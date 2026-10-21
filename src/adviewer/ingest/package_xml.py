"""Index ROS packages in a source tree: name -> directory, and infer the Autoware layer."""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from adviewer.model import Graph, Layer

_LAYER_DIRS: dict[str, Layer] = {
    lay.value: lay for lay in Layer if lay not in (Layer.external, Layer.unknown)
}
# autoware_launch keeps sensor kits and vehicles in its own top-level dirs (1.9.0)
_LAYER_DIRS["sensor_kit"] = Layer.sensing
_TIER4_LAUNCH = re.compile(r"^tier4_(\w+?)_launch$")  # tier4_sensing_launch -> sensing


@dataclass
class PackageInfo:
    name: str
    path: Path  # directory containing package.xml
    layer: Layer
    rel_path: str  # relative to autoware root
    version: str = ""


def layer_from_path(rel_path: str, name: str = "") -> Layer:
    """Layer = the Autoware top-level directory that holds the package (F8).

    Works for 'universe/autoware_universe/system/mrm_handler' and
    'core/autoware_core/planning/...'. Launch packages are named after their layer:
    tier4_<layer>_launch (tier4_autoware_api_launch -> api); autoware_launch -> launch.
    """
    m = _TIER4_LAUNCH.match(name)
    if m:
        key = "api" if m.group(1) == "autoware_api" else m.group(1)
        if key in _LAYER_DIRS:
            return _LAYER_DIRS[key]
    if name == "autoware_launch":
        return Layer.launch
    for part in Path(rel_path).parts:
        if part in _LAYER_DIRS:
            return _LAYER_DIRS[part]
    return Layer.unknown

def _is_ignored(pkg_dir: Path, root: Path) -> bool:
    """True if pkg_dir, or any directory between it and root, contains COLCON_IGNORE.

    root itself and everything above it are not checked.
    """
    d = pkg_dir
    while d != root and d != d.parent:
        if (d/ "COLCON_IGNORE").exists():
            return True
        d = d.parent
    return False


def index_packages(root: Path, graph: Graph | None = None) -> dict[str, PackageInfo]:
    """Find every package.xml under root. COLCON_IGNORE'd dirs are skipped."""
    root = Path(root)
    packages: dict[str, PackageInfo] = {}
    for pkg_xml in root.rglob("package.xml"):
        pkg_dir = pkg_xml.parent
        if _is_ignored(pkg_dir, root):
            continue
        try:
            xml_root = ET.parse(pkg_xml).getroot()
        except ET.ParseError as exc:
            if graph:
                graph.warn(f"{pkg_xml.relative_to(root).as_posix()}: package.xml parse error: {exc}")
            continue
        name_el = xml_root.find("name")
        if name_el is None or not (name_el.text or "").strip():
            if graph:
                graph.warn(f"{pkg_xml.relative_to(root).as_posix()}: package.xml has no <name>")
            continue
        name = name_el.text.strip()  # type: ignore[union-attr]
        rel = pkg_dir.relative_to(root).as_posix()  # "/" on every OS: evidence is portable
        version = (xml_root.findtext("version") or "").strip()
        packages[name] = PackageInfo(
            name=name, path=pkg_dir, layer=layer_from_path(rel, name), rel_path=rel, version=version
        )
    return packages

# Folders holding test or example code/data, not what runs (Phase 1 F2b, F7). A name
# heuristic; for diag YAML the proper fix is F9.
_NON_RUNTIME_DIRS = {"test", "tests", "example", "examples"}


def _is_non_runtime(path: Path, pkg_dir: Path) -> bool:
    return any(part in _NON_RUNTIME_DIRS for part in path.relative_to(pkg_dir).parts)
