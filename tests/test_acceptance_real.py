"""Offline acceptance rows of the requirements, on a real Autoware tree of a supported release.

The tree is ~/autoware-1.9.0 or the folder in ADVIEWER_AUTOWARE_TREE: `install-space/` + `src/`
(from `adviewer extract` and `fetch-source`), or only `src/` (from `adviewer fetch-source`: no
Docker; the weekly CI job reads every supported release this way). ADVIEWER_AUTOWARE_RELEASE
names the release (default 1.9.0). Skipped where no tree is present.
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
from adviewer.project import DEFAULT_ENTRY

RELEASE = os.environ.get("ADVIEWER_AUTOWARE_RELEASE", "1.9.0")
AW = Path(os.environ.get("ADVIEWER_AUTOWARE_TREE") or Path.home() / f"autoware-{RELEASE}")
INSTALL = AW / "install-space" if (AW / "install-space" / DEFAULT_ENTRY).is_file() else AW / "src"
pytestmark = pytest.mark.skipif(not (AW / "src").is_dir(), reason=f"no Autoware {RELEASE} tree in {AW}")


@pytest.fixture(scope="module")
def g() -> Graph:
    return ingest_autoware_tree(INSTALL, str(INSTALL / DEFAULT_ENTRY), AW / "src")  # entry found by package


@pytest.fixture(scope="module")
def data() -> dict:
    rel = next(r for r in available_releases() if r.release == RELEASE)
    return {name: rel.load(f"{name}.yaml") for name in ("aliases", "mrm", "subsystems")}


def test_the_release_is_recognised_and_complete(g: Graph, data: dict) -> None:
    assert g.meta.autoware_release == RELEASE and g.meta.entry_package == "autoware_launch"
    assert len(g.nodes) > 100 and len(g.diag_units) > 50
    assert not [n for n in g.nodes.values() if n.layer.value == "unknown"]  # layers.yaml covers every node
    for topic in [*data["subsystems"]["subsystems"].values(), *data["mrm"]["chain"]]:
        assert g.publishers(topic), f"{topic}: no publisher at {RELEASE}"
    in_tree = {m.split("/")[0] for m in g.msg_types}  # message packages read from this tree (ROS's own are not in it)
    used = {t.msg_type for t in g.topics.values() if t.msg_type and t.msg_type.split("/")[0] in in_tree}
    assert used and not used - set(g.msg_types)  # e.g. MrmState, kept in a subfolder of its source package


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
    assert re.search(r"autoware_launch/config/planning/scenario_planning/common/common\.param\.yaml:\d+$", hit["evidence"])
    copy = next(r["how"] for r in hit["routes"] if "COPY" in r["how"])  # also when read from source
    assert copy.endswith("/opt/autoware/autoware_launch/share/autoware_launch/config/planning/scenario_planning/common/common.param.yaml")
