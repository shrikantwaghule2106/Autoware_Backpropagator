"""Search, expand/trace, shutdown impact, MRM gates, details (Phase 2). Pure functions."""
import pytest

from adviewer.analysis.details import node_details, topic_details
from adviewer.analysis.explore import expand, kit_nodes, search, trace
from adviewer.analysis.impact import mrm_gates, shutdown_impact
from adviewer.model import (
    Confidence,
    DiagUnit,
    Direction,
    Edge,
    Graph,
    Node,
    Param,
    ParamKind,
)

RULES = {
    "aggregator_package": "agg", "mode_mappings_param": "command_mode_mappings",
    "mode_names": {1002: "autonomous", 2002: "comfortable_stop"}, "handler_package": "mrm",
    "trigger_mode": "autonomous", "trigger_rule": "r", "chain": ["/availability"],
}


@pytest.fixture
def g() -> Graph:
    g = Graph()
    for nid, pkg, src in [("/sensing/lidar/driver", "drv", "kit_launch/launch/lidar.launch.xml"),
                          ("/sensing/lidar/filter", "flt", "kit_launch/launch/lidar.launch.xml"),
                          ("/perception/detector", "det", "p/launch/p.launch.xml"),
                          ("/sensing/imu/driver", "imu", "kit_launch/launch/imu.launch.xml"),
                          ("/localization/ekf", "ekf", "l/launch/l.launch.xml"),
                          ("/system/topic_state_monitor_objects", "autoware_topic_state_monitor", "s.xml"),
                          ("/system/aggregator", "agg", "s.xml"), ("/system/mrm_handler", "mrm", "s.xml")]:
        g.add_node(Node(id=nid, package=pkg, source_file=src))
    wires = [("/sensing/lidar/driver", "/sensing/lidar/raw", "p"), ("/sensing/lidar/filter", "/sensing/lidar/raw", "s"),
             ("/sensing/lidar/filter", "/sensing/lidar/points", "p"), ("/perception/detector", "/sensing/lidar/points", "s"),
             ("/perception/detector", "/perception/objects", "p"), ("/sensing/imu/driver", "/sensing/imu/data", "p"),
             ("/localization/ekf", "/sensing/imu/data", "s"), ("/localization/ekf", "/diagnostics", "p"),
             ("/perception/detector", "/diagnostics", "p"), ("/system/aggregator", "/diagnostics", "p"),
             ("/system/topic_state_monitor_objects", "/perception/objects", "s")]
    for node, topic, d in wires:
        g.add_edge(Edge(node=node, topic=topic, direction=Direction.publish if d == "p" else Direction.subscribe,
                        confidence=Confidence.static_source, evidence=f"f.xml:{len(g.edges) + 1}",
                        qos={"reliability": "best_effort"}))
    g.topics["/sensing/lidar/points"].msg_type = "sensor_msgs/msg/PointCloud2"
    units = [DiagUnit(path="/autoware/modes/autonomous", type="and", children=["/autoware/perception", "/autoware/loc"]),
             DiagUnit(path="/autoware/perception", type="or", children=["/autoware/perception/objects", "/autoware/perception/alt"]),
             DiagUnit(path="/autoware/perception/objects", type="diag", diag_name="topic_state_monitor_objects: perception_topic_status"),
             DiagUnit(path="/autoware/perception/alt", type="diag", diag_name="detector: alive"),
             DiagUnit(path="/autoware/loc", type="diag", diag_name="ekf: status"),
             DiagUnit(path="/autoware/modes/comfortable_stop", type="and", children=["/autoware/loc"])]
    g.diag_units = {u.path: u for u in units}
    g.params.append(Param(node="/system/aggregator", name="command_mode_mappings", kind=ParamKind.param_file,
                          value='["{mode: 1002, path: /autoware/modes/autonomous}", "{mode: 2002, path: /autoware/modes/comfortable_stop}"]',
                          evidence="default.param.yaml:12"))
    g.params.append(Param(node="/system/mrm_handler", name="use_comfortable_stop", value="true",
                          kind=ParamKind.param_file, evidence="mrm.yaml:3"))
    return g


def test_search_partial_alias_and_kinds(g: Graph) -> None:
    hits = search(g, "lid", {"lidar": ["pointcloud", "points"]}, {"kit": ["kit_launch"]})
    assert hits[0]["kind"] == "topic" and "lidar" in hits[0]["label"]
    assert any(h["kind"] == "node" for h in hits)
    assert [h["label"] for h in search(g, "kit", {}, {"kit": ["kit_launch"]}) if h["kind"] == "sensor_kit"] == ["kit"]
    assert search(g, "use_comf", {}, {})[0]["kind"] == "param"
    assert search(g, "   ", {}, {}) == []


def test_expand_one_level_each_way(g: Graph) -> None:
    back = expand(g, "t:/sensing/lidar/points", "back")
    assert [n["id"] for n in back["nodes"]] == ["n:/sensing/lidar/filter"]
    assert back["nodes"][0]["more"] is True
    assert (back["nodes"][0]["inputs"], back["nodes"][0]["consumers"]) == (1, 1)  # the "+n" counts
    assert back["root"]["inputs"] == 1 and back["root"]["consumers"] == 1
    assert back["edges"][0]["source"] == "n:/sensing/lidar/filter"  # data-flow direction
    fwd = expand(g, "t:/sensing/lidar/points", "fwd")
    assert [n["id"] for n in fwd["nodes"]] == ["n:/perception/detector"]


def test_trace_depth_and_external_flag(g: Graph) -> None:
    t = trace(g, "t:/perception/objects", "back", 4)
    levels = {n["id"]: n["level"] for n in t["nodes"]}
    assert levels["t:/sensing/lidar/raw"] == -4 and levels["n:/perception/detector"] == -1


def test_shutdown_impact_and_mrm(g: Graph) -> None:
    kit = kit_nodes(g, ["kit_launch"])
    assert set(kit) == {"/sensing/lidar/driver", "/sensing/lidar/filter", "/sensing/imu/driver"}
    r = shutdown_impact(g, [], kit, RULES)
    affected = {a["id"] for a in r["affected_nodes"]}
    assert {"/perception/detector", "/localization/ekf", "/system/topic_state_monitor_objects"} <= affected
    assert "/diagnostics" not in r["stopped_topics"]  # the aggregator still publishes it
    assert {d["path"] for d in r["failed_diagnostics"]} == {
        "/autoware/perception/objects", "/autoware/perception/alt", "/autoware/loc"}
    assert r["mrm"]["triggered"] and r["mrm"]["behaviour"] == "emergency_stop"  # comfortable stop unavailable


def test_or_unit_survives_one_failed_child_and_comfortable_stop_chosen(g: Graph) -> None:
    r = shutdown_impact(g, ["/perception/objects"], [], RULES)
    assert "/autoware/perception" not in r["failed_units"]  # or: detector still alive
    assert r["mrm"]["triggered"] is False
    r = shutdown_impact(g, ["/sensing/lidar/raw"], [], RULES)
    assert r["mrm"]["triggered"] and r["mrm"]["behaviour"] == "comfortable_stop"


def test_mrm_gates_tree(g: Graph) -> None:
    gates = mrm_gates(g, RULES)
    assert gates["unit"] == "/autoware/modes/autonomous"
    leaves = gates["tree"]["children"][0]["children"]
    assert leaves[0]["node"] == "/system/topic_state_monitor_objects"


def test_details(g: Graph) -> None:
    t = topic_details(g, "/sensing/lidar/points")
    assert t is not None and t["msg_type"] == "sensor_msgs/msg/PointCloud2"
    assert t["publishers"][0]["qos"] == {"reliability": "best_effort"}
    n = node_details(g, "/localization/ekf")
    assert n is not None and n["diagnostics"] == ["/autoware/loc"]
    assert topic_details(g, "/nope") is None
