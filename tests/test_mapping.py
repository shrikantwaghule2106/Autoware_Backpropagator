"""Mapping assistant (Phase 5): assignment, coverage, readiness."""
from adviewer.analysis.mapping import (
    assign,
    mapping_report,
    required_inputs,
    signals_from_descriptor,
    to_markdown,
)
from adviewer.model import Confidence, Direction, Edge, Graph, Node, Rate


def _g() -> Graph:
    g = Graph()
    g.add_node(Node(id="/seg", package="seg"))
    g.add_node(Node(id="/ptc", package="autoware_processing_time_checker"))
    for node, topic, d, qos in [
        ("/seg", "/sensing/lidar/raw", Direction.subscribe, {"reliability": "reliable"}),
        ("/seg", "/vehicle/status/velocity_status", Direction.subscribe, {}),
        ("/seg", "/perception/objects", Direction.publish, {}),
        ("/ptc", "/seg/debug/processing_time_ms", Direction.subscribe, {}),
        ("/ptc", "/x/time", Direction.subscribe, {}),
    ]:
        g.add_edge(Edge(node=node, topic=topic, direction=d, confidence=Confidence.static_source,
                        evidence="f:1", qos=qos))
    g.topics["/sensing/lidar/raw"].msg_type = "sensor_msgs/msg/PointCloud2"
    g.topics["/vehicle/status/velocity_status"].msg_type = "autoware_vehicle_msgs/msg/VelocityReport"
    g.topics["/vehicle/status/velocity_status"].rates.append(
        Rate(hz=5.0, kind="monitor", evidence="t.yaml:3", note="warn below 5.0 Hz"))
    return g


DESC = {"target": "sim", "provides": [
    {"name": "lidar", "ros_type": "sensor_msgs/msg/PointCloud2", "rate_hz": 10},
    {"name": "velocity", "ros_type": "autoware_vehicle_msgs/msg/VelocityReport", "rate_hz": 50},
    {"name": "camera", "ros_type": "sensor_msgs/msg/Image"},
]}


def test_required_inputs_skip_debug_and_monitor_only() -> None:
    assert required_inputs(_g()) == ["/sensing/lidar/raw", "/vehicle/status/velocity_status"]


def test_assignment_type_match_and_monitor_rate_is_a_minimum() -> None:
    r = assign(_g(), signals_from_descriptor(DESC))
    pairs = {p["signal"]: p["topic"] for p in r["pairs"]}
    assert pairs == {"lidar": "/sensing/lidar/raw", "velocity": "/vehicle/status/velocity_status"}
    vel = next(p for p in r["pairs"] if p["signal"] == "velocity")
    assert vel["terms"]["rate"] == 0.0  # 50 Hz ≥ the 5 Hz minimum: no penalty
    assert r["unused_signals"] == ["camera"] and r["coverage"] == 1.0


def test_qos_incompatibility_is_a_hard_fail() -> None:
    desc = {"provides": [{"name": "lidar", "ros_type": "sensor_msgs/msg/PointCloud2",
                          "qos": {"reliability": "best_effort"}}]}  # subscriber requires reliable
    r = assign(_g(), signals_from_descriptor(desc))
    assert r["pairs"] == [] and "/sensing/lidar/raw" in r["unassigned_required"]


def test_report_readiness_and_markdown() -> None:
    g = _g()
    r = mapping_report(g, {"target": "sim", "signals": DESC["provides"][:1]},
                       {"perception": "/perception/objects"})
    assert r["readiness"]["perception"]["readiness"] == 0.5  # 1 of 2 equally weighted inputs
    md = to_markdown(r, g)
    assert "| ✓ | `/sensing/lidar/raw`" in md and "| ☐ | `/vehicle/status/velocity_status`" in md
