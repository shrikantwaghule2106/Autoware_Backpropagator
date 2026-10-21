from pathlib import Path

from adviewer.ingest import ingest_autoware_tree
from adviewer.ingest.cpp_source import Endpoint, index_endpoints, scan_cpp
from adviewer.ingest.package_xml import index_packages
from adviewer.model import Confidence, Direction, Graph

FIX = Path(__file__).parent / "fixtures" / "cpp" / "demo_node.cpp"
MINI = Path(__file__).parent / "fixtures" / "mini_cpp"


def _scan() -> dict[str, Endpoint]:
    return {e.name: e for e in scan_cpp(FIX.read_text())}


def test_literal_endpoints_are_found_and_computed_names_skipped() -> None:
    assert set(_scan()) == {"velocity_status", "twist_with_covariance", "~/debug/value"}


def test_subscription_split_over_lines() -> None:
    e = _scan()["velocity_status"]
    assert (e.kind, e.msg_type, e.qos, e.line) == (
        "subscription", "autoware_vehicle_msgs/msg/VelocityReport", "rclcpp::QoS{10}", 5)


def test_publisher_via_this_with_chained_qos() -> None:
    e = _scan()["twist_with_covariance"]
    assert e.kind == "publisher"
    assert e.qos == "rclcpp::QoS(1).transient_local()"


def test_alias_type_is_not_guessed() -> None:
    e = _scan()["~/debug/value"]
    assert e.msg_type == ""
    assert e.qos == "rclcpp::SensorDataQoS()"

def test_index_skips_test_folders() -> None:
    idx = index_endpoints(index_packages(MINI))
    assert "vel_conv/test/test_vel_conv.cpp" not in {f for f, _ in idx["vel_conv"]}


def test_package_aliases_resolve_message_types() -> None:  # F7b
    eps = {ep.name: ep for _, ep in index_endpoints(index_packages(MINI))["vel_conv"]}
    assert eps["~/output/aliased"].msg_type == "autoware_vehicle_msgs/msg/VelocityReport"
    assert eps["~/input/pose"].msg_type == "geometry_msgs/msg/PoseStamped"  # typedef
    assert eps["~/input/clash"].msg_type == ""  # declared twice with different types


def _edge(graph: Graph, topic: str):
    return next(e for e in graph.edges if e.topic == topic)


def test_direction_from_source_when_name_says_nothing() -> None:
    graph = ingest_autoware_tree(MINI)
    sub = _edge(graph, "/vehicle/status/velocity_status")
    assert (sub.node, sub.direction, sub.confidence) == (
        "/sensing/vel_conv", Direction.subscribe, Confidence.static_source)
    assert "vel_conv/src/vel_conv_node.cpp:" in sub.evidence
    pub = _edge(graph, "/sensing/vehicle_velocity_converter/twist_with_covariance")
    assert pub.direction == Direction.publish
    assert graph.topics[pub.topic].msg_type == "geometry_msgs/msg/TwistWithCovarianceStamped"


def test_no_literal_in_source_stays_unknown() -> None:
    graph = ingest_autoware_tree(MINI)
    assert not any(e.topic == "/sensing/debug" for e in graph.edges)
    assert any("direction unknown" in w for w in graph.meta.warnings)


def test_computed_name_does_not_bridge_into_next_call() -> None:
    eps = {e.name: e for e in scan_cpp((FIX.parent / "bridging.cpp").read_text())}
    assert (eps["~/input/twist"].kind, eps["~/input/twist"].msg_type) == (
        "subscription", "geometry_msgs/msg/TwistWithCovarianceStamped")
    assert (eps["~/input/text"].kind, eps["~/input/text"].msg_type) == ("subscription", "std_msgs/msg/String")
