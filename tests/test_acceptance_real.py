"""Offline acceptance rows of the requirements, on the real pinned tree (1.9.0).

Skipped where the tree (install space + source companion) is not present: ~/autoware-1.9.0, or
the folder in ADVIEWER_AUTOWARE_TREE (the weekly CI job extracts it from the official image).
"""
import os
import re
from pathlib import Path

import pytest

from adviewer.analysis.explore import kit_nodes, search, trace
from adviewer.analysis.impact import mrm_gates, shutdown_impact
from adviewer.analysis.where_to_edit import find_params
from adviewer.ingest import ingest_autoware_tree
from adviewer.ingest.release_data import available_releases
from adviewer.model import Direction, Graph

AW = Path(os.environ.get("ADVIEWER_AUTOWARE_TREE") or Path.home() / "autoware-1.9.0")
ENTRY = AW / "install-space/autoware_launch/share/autoware_launch/launch/autoware.launch.xml"
pytestmark = pytest.mark.skipif(not ENTRY.is_file(), reason="pinned Autoware 1.9.0 tree not present")


@pytest.fixture(scope="module")
def g() -> Graph:
    return ingest_autoware_tree(AW / "install-space", str(ENTRY), AW / "src")


@pytest.fixture(scope="module")
def data() -> dict:
    rel = next(r for r in available_releases() if r.release == "1.9.0")
    return {"aliases": rel.load("aliases.yaml"), "mrm": rel.load("mrm.yaml")}


def test_search_and_trace_lidar(g: Graph, data: dict) -> None:
    hits = search(g, "lidar", data["aliases"]["aliases"], data["aliases"]["sensor_kits"])
    assert hits and all(h["kind"] == "topic" and "lidar" in h["label"] for h in hits[:5])
    back = trace(g, "t:/sensing/lidar/concatenated/pointcloud", "back", 12)
    ids = {n["id"] for n in back["nodes"]}
    kit = set(kit_nodes(g, data["aliases"]["sensor_kits"]["sample_sensor_kit"]))
    assert {f"n:{n}" for n in kit} & ids  # reaches the sensor kit's nodes
    assert "t:/sensing/lidar/top/pointcloud_raw_ex" in ids  # and the driver output
    fwd = trace(g, "t:/sensing/lidar/concatenated/pointcloud", "fwd", 2)
    assert len(fwd["nodes"]) > 1
    assert all(e["evidence"] for e in back["edges"] + fwd["edges"])  # each edge with evidence


@pytest.mark.parametrize(("sensor", "pattern"), [
    ("lidar", r"^/sensing/lidar/"), ("imu", r"^/sensing/imu/"), ("gnss", r"^/sensing/gnss/"),
    ("vehicle velocity", r"^/sensing/vehicle_velocity_converter/|^/vehicle/status/velocity_status$"),
])
def test_every_launched_sensor_is_traceable(g: Graph, sensor: str, pattern: str) -> None:
    # camera and radar are not launched by the sample kit with perception_mode=lidar
    consumers = {e.node for e in g.edges
                 if e.direction == Direction.subscribe and re.search(pattern, e.topic)}
    assert consumers, sensor


def test_shutdown_impact_of_the_sensor_kit(g: Graph, data: dict) -> None:
    kit = kit_nodes(g, data["aliases"]["sensor_kits"]["sample_sensor_kit"])
    r = shutdown_impact(g, [], kit, data["mrm"])
    assert len(r["affected_nodes"]) > len(kit) and r["failed_diagnostics"]
    assert r["mrm"]["triggered"]


def test_mrm_shows_the_gating_diagnostics(g: Graph, data: dict) -> None:
    gates = mrm_gates(g, data["mrm"])
    assert gates["unit"] == "/autoware/modes/autonomous"
    leaves = []

    def walk(n: dict) -> None:
        if n["type"] == "diag":
            leaves.append(n)
        for c in n.get("children", []):
            walk(c)

    walk(gates["tree"])
    assert len(leaves) > 10 and any(leaf["node"] for leaf in leaves)


def test_where_to_edit_max_velocity(g: Graph) -> None:
    [hit] = [h for h in find_params(g, "max_vel") if h["name"] == "max_vel"]
    assert hit["value"] == "4.17"
    assert hit["evidence"].endswith("config/planning/scenario_planning/common/common.param.yaml:16")
    assert any("COPY" in r["how"] for r in hit["routes"])
