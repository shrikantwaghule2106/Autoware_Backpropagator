from pathlib import Path

import pytest

from adviewer.analysis import criticality, drilldown
from adviewer.ingest import ingest_autoware_tree
from adviewer.model import Confidence, Direction, Graph, Layer

FIX = Path(__file__).parent / "fixtures" / "mini_autoware"


@pytest.fixture(scope="module")
def graph() -> Graph:
    return ingest_autoware_tree(FIX, entry_launch=str(FIX / "launch/top/launch/autoware.launch.xml"))


def test_nodes_and_layers(graph: Graph) -> None:
    assert "/system/mrm_handler" in graph.nodes
    assert graph.nodes["/system/mrm_handler"].layer == Layer.system
    assert graph.nodes["/system/mrm_handler"].package == "mrm_handler"
    assert "/system/diagnostic_graph_aggregator" in graph.nodes  # via push-ros-namespace


def test_include_arg_override(graph: Graph) -> None:
    # <include><arg value=…> must override the <arg default=…> in the included file
    assert "/api/fail_safe/mrm_state" in graph.topics
    assert "/system/fail_safe/mrm_state" not in graph.topics


def test_direction_convention(graph: Graph) -> None:
    pubs = {e.node for e in graph.publishers("/system/operation_mode/availability")}
    subs = {e.node for e in graph.subscribers("/system/operation_mode/availability")}
    assert pubs == {"/system/diagnostic_graph_aggregator"}
    assert subs == {"/system/mrm_handler"}
    assert all(e.confidence == Confidence.static_convention for e in graph.edges)


def test_unknown_direction_is_warning_not_edge(graph: Graph) -> None:
    assert "/system/unknown_direction" in graph.topics
    assert not any(e.topic == "/system/unknown_direction" for e in graph.edges)
    assert any("direction unknown" in w for w in graph.meta.warnings)
    assert any("unknown package 'missing_pkg'" in w for w in graph.meta.warnings)


def test_external_inputs(graph: Graph) -> None:
    ext = graph.external_inputs()
    assert "/diagnostics" in ext
    assert "/localization/kinematic_state" in ext
    assert "/system/operation_mode/availability" not in ext  # published internally


def test_msg_parsing(graph: Graph) -> None:
    m = graph.msg_types["sys_msgs/msg/MrmState"]
    names = [f.name for f in m.fields]
    assert names == ["stamp", "state", "behavior", "reserved", "notes"]
    assert m.constants == {"NORMAL": "1", "MRM_OPERATING": "2"}
    stamp = m.fields[0]
    assert stamp.type == "builtin_interfaces/msg/Time"
    assert m.fields[3].is_array and m.fields[3].array_len == 3
    assert m.fields[4].is_array and m.fields[4].array_len is None


def test_diag_graph(graph: Graph) -> None:
    assert "/autoware/modes/autonomous" in graph.diag_units
    root = graph.diag_units["/autoware/modes/autonomous"]
    assert root.type == "and" and len(root.children) == 2
    assert "/autoware/sensing/lidar" in graph.diag_units  # from files: sub.yaml


def test_edge_upgrade() -> None:
    from adviewer.model import Edge

    g = Graph()
    e1 = Edge(node="/n", topic="/t", direction=Direction.publish, confidence=Confidence.static_convention, evidence="a")
    e2 = Edge(node="/n", topic="/t", direction=Direction.publish, confidence=Confidence.live, evidence="probe")
    g.add_edge(e1)
    g.add_edge(e2)
    g.add_edge(e1)  # lower confidence must not downgrade
    assert len(g.edges) == 1 and g.edges[0].confidence == Confidence.live


def test_roundtrip(tmp_path: Path, graph: Graph) -> None:
    p = tmp_path / "g.json"
    graph.save(p)
    g2 = Graph.load(p)
    assert g2.nodes.keys() == graph.nodes.keys() and len(g2.edges) == len(graph.edges)


def test_analysis(graph: Graph) -> None:
    d = drilldown(graph, "/api/fail_safe/mrm_state", depth=2)
    assert "/diagnostics" in d["upstream_external_inputs"]
    c = criticality(graph, "/api/fail_safe/mrm_state")
    assert "n:/system/mrm_handler" in c["articulation_points"]
    assert c["min_cut_size"] >= 1


def test_api(graph: Graph) -> None:
    from fastapi.testclient import TestClient

    from adviewer.api.app import create_app

    c = TestClient(create_app(graph))
    assert c.get("/api/meta").json()["counts"]["nodes"] == len(graph.nodes)
    assert c.get("/api/topic", params={"name": "/diagnostics"}).status_code == 200
    els = c.get("/api/graph", params={"layer": "system"}).json()["elements"]
    assert any(e["data"].get("external") for e in els)
    assert c.get("/").status_code == 200

def test_example_diag_config_is_ignored(graph: Graph) -> None:
    unit = graph.diag_units["/autoware/modes/autonomous"]
    assert unit.type == "and"
    assert "example" not in unit.source_file


OVERLAY = Path(__file__).parent / "fixtures" / "overlay"


def test_overlay_package_replaces_base_package() -> None:  # D13: roots in overlay order
    g = ingest_autoware_tree(
        OVERLAY / "base",
        entry_launch=str(OVERLAY / "base/top/share/top/launch/top.launch.xml"),
        overlays=[OVERLAY / "user_ws"],
    )
    assert "/from_overlay" in g.nodes and "/from_base" not in g.nodes
    assert g.nodes["/from_overlay"].source_file == "user_ws/pkg_a/share/pkg_a/launch/a.launch.xml"


def test_messages_in_subfolders_of_a_source_package() -> None:
    """autoware_adapi_v1_msgs keeps system/msg/MrmState.msg in its source (installed as msg/):
    reading Autoware from its source must find it too; .msg files outside a msg/ folder are not messages."""
    g = ingest_autoware_tree(Path(__file__).parent / "fixtures" / "msg_subdirs")
    assert {"adapi_msgs/msg/MrmState", "adapi_msgs/msg/Plain"} <= set(g.msg_types)
    assert "adapi_msgs/msg/NotAMsg" not in g.msg_types
    assert [f.name for f in g.msg_types["adapi_msgs/msg/MrmState"].fields] == ["state"]


def test_entry_launch_in_install_form_is_found_in_a_source_tree(graph: Graph) -> None:
    """One adviewer.yaml for both layouts: <pkg>/share/<pkg>/launch/… is looked up by package."""
    g = ingest_autoware_tree(FIX, entry_launch=str(FIX / "top_launch/share/top_launch/launch/autoware.launch.xml"))
    assert g.meta.entry_launch == "launch/top/launch/autoware.launch.xml" and g.meta.entry_package == "top_launch"
    assert set(g.nodes) == set(graph.nodes) and len(g.edges) == len(graph.edges)
    missing = ingest_autoware_tree(FIX, entry_launch=str(FIX / "nope/share/nope/launch/x.launch.xml"))
    assert any("entry launch not found" in w for w in missing.meta.warnings)
