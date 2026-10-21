"""Fault corpus: setups broken on purpose, and what adviewer must report for each.

Like the bug benchmarks of architecture-recovery tools, this is the list of faults the tool
catches, kept as tests:

| Fault                                                     | Where it shows                       |
|-----------------------------------------------------------|--------------------------------------|
| a remap typo: a node waits for a topic nobody publishes   | external inputs (no publisher)       |
| an include that does not exist                            | warning "include not found"          |
| a package that is not installed ($(find-pkg-share))       | warning "unknown package"            |
| a parameter file that is missing                          | warning "parameter file not found"   |
| a diagnostic-graph file of the wrong shape                | warning, no crash                    |
| best-effort publisher, reliable subscriber                | live: QoS incompatible               |
| a different message type at run time                      | live: type mismatch                  |
| a monitored topic slower than its threshold               | live: below rate, health degraded    |
| a topic the launch promises, missing while its node runs  | live: missing topic, publisher runs  |
| a sensor driver stops                                     | dashboard: "its source … stopped"    |
| a launch slows an unconnected topic                       | dashboard: root, not connected, load |
"""
from pathlib import Path

from adviewer.analysis.live import annotate
from adviewer.analysis.timeline import timeline
from adviewer.ingest import ingest_autoware_tree
from adviewer.ingest.diag_graph_yaml import parse_diag_graph_file
from adviewer.model import Confidence, Direction, Edge, Graph, Rate

FIX = Path(__file__).parent / "fixtures"
PKG = '<?xml version="1.0"?><package format="3"><name>{}</name></package>\n'


def _tree(root: Path, launch: str, packages: tuple[str, ...] = ("top", "drv", "flt")) -> Graph:
    for name in packages:
        share = root / name / "share" / name
        share.mkdir(parents=True)
        (share / "package.xml").write_text(PKG.format(name), encoding="utf-8")
    entry = root / "top" / "share" / "top" / "launch" / "top.launch.xml"
    entry.parent.mkdir(parents=True)
    entry.write_text(launch, encoding="utf-8")
    return ingest_autoware_tree(root, entry_launch=str(entry))


def _warned(g: Graph, text: str) -> bool:
    return any(text in w for w in g.meta.warnings)


def test_remap_typo_leaves_a_consumer_without_publisher(tmp_path: Path) -> None:
    g = _tree(tmp_path, """<launch>
  <node pkg="drv" exec="driver" name="lidar_driver"><remap from="~/output/points" to="/sensing/lidar/points"/></node>
  <node pkg="flt" exec="filter" name="filter"><remap from="~/input/points" to="/sensing/lidar/point"/></node>
</launch>""")
    assert "/sensing/lidar/point" in g.external_inputs()  # what the filter waits for: nobody publishes it
    assert not list(g.subscribers("/sensing/lidar/points"))  # and what the driver sends: nobody reads it


def test_missing_include_unknown_package_and_missing_parameter_file(tmp_path: Path) -> None:
    g = _tree(tmp_path, """<launch>
  <include file="$(find-pkg-share top)/launch/gone.launch.xml"/>
  <include file="$(find-pkg-share ghost)/launch/x.launch.xml"/>
  <node pkg="drv" exec="driver" name="d"><param from="$(find-pkg-share top)/config/missing.param.yaml"/></node>
</launch>""")
    assert _warned(g, "include not found")
    assert _warned(g, "unknown package 'ghost'")
    assert _warned(g, "parameter file not found")


def test_diagnostic_graph_of_the_wrong_shape_warns() -> None:
    g = Graph()
    parse_diag_graph_file(FIX / "diag_invalid" / "items_not_mapping.yaml", g)
    assert g.meta.warnings


def _live_graph() -> Graph:
    g = Graph()
    for node, topic, d in [("/drv", "/sensing/lidar/points", Direction.publish), ("/seg", "/sensing/lidar/points", Direction.subscribe),
                           ("/planner", "/planning/trajectory", Direction.publish)]:
        g.add_edge(Edge(node=node, topic=topic, direction=d, confidence=Confidence.static_source, evidence="a.launch.xml:1"))
    g.topics["/sensing/lidar/points"].msg_type = "sensor_msgs/msg/PointCloud2"
    g.topics["/sensing/lidar/points"].rates.append(
        Rate(hz=8.0, kind="monitor", evidence="m.yaml:1", note="m: warn below 8.0 Hz, error below 1.0 Hz, timeout 1.0 s"))
    return g


def _ep(node: str, reliability: str) -> dict:
    return {"node": node, "type": "x", "qos": {"reliability": reliability, "durability": "volatile"}}


def test_live_faults_qos_type_rate_and_missing_topic() -> None:
    g = _live_graph()
    report = annotate(g, {"captured_at": "2026-10-20T10:00:00Z", "window_s": 5.0, "nodes": ["/drv", "/seg", "/planner"],
                          "topics": [{"name": "/sensing/lidar/points", "types": ["std_msgs/msg/String"], "measured": True,
                                      "rate_hz": 4.0, "count": 20, "publishers": [_ep("/drv", "best_effort")],
                                      "subscribers": [_ep("/seg", "reliable")]}]})
    assert report["qos_incompatible"][0]["publisher"] == "/drv"  # best effort cannot serve a reliable reader
    assert report["type_mismatches"][0]["live"] == ["std_msgs/msg/String"]
    assert report["below_rate"][0]["expected_hz"] == 8.0 and g.topics["/sensing/lidar/points"].health == "degraded"
    [missing] = report["missing_topics"]
    assert missing["topic"] == "/planning/trajectory" and missing["publisher_running"]  # its node runs, the topic is absent


def _w(k: int, nodes: list[str], rates: dict[str, float], cpu: float) -> dict:
    return {"t": f"2026-10-20T10:00:{k * 5:02d}Z", "window_s": 5.0, "nodes": nodes,
            "topics": {t: [int(r * 5), r, 1.0, 100.0, 1e6, 1 if r else 0] for t, r in rates.items()}, "res": {"cpu": cpu}}


def test_dashboard_faults_stopped_source_and_unconnected_slowdown() -> None:
    g = _live_graph()
    stop = [_w(k, ["/drv", "/seg"] if k < 5 else ["/seg"], {"/sensing/lidar/points": 10.0 if k < 5 else 0.0}, 40.0) for k in range(9)]
    [c] = [c for c in timeline(g, stop)["changes"] if c["status"] == "affected"]
    assert "Its source /drv stopped" in c["answer"]
    slow = [_w(k, ["/drv", "/seg"] + (["/camera"] if k >= 5 else []), {"/sensing/lidar/points": 10.0 if k < 5 else 6.0},
               40.0 if k < 5 else 90.0) for k in range(10)]
    [c] = [c for c in timeline(g, slow)["changes"] if c["status"] == "affected"]
    root = c["roots"][0]
    assert root["relation"] == "unconnected" and "CPU 40 → 90 %" in c["answer"]
