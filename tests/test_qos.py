"""QoS read from C++ text (F7b); presets from Jazzy rmw/qos_profiles.h."""
import pytest

from adviewer.ingest.qos import incompatible, interpret_qos


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("rclcpp::QoS{10}", {"reliability": "reliable", "durability": "volatile", "history": "keep_last", "depth": "10"}),
        ("1", {"reliability": "reliable", "depth": "1"}),
        ("rclcpp::SensorDataQoS()", {"reliability": "best_effort", "depth": "5"}),
        ("rclcpp::SensorDataQoS().keep_last(params_.max)", {"reliability": "best_effort", "depth": "?"}),
        ("rclcpp::QoS(1).transient_local()", {"durability": "transient_local", "depth": "1"}),
        ("rclcpp::QoS(rclcpp::KeepLast(3)).best_effort()", {"reliability": "best_effort", "depth": "3"}),
        ("rclcpp::QoS{1}.reliability(rclcpp::ReliabilityPolicy::BestEffort)", {"reliability": "best_effort"}),
        ("rclcpp::QoS(rclcpp::KeepAll())", {"history": "keep_all"}),
        ("rclcpp::SystemDefaultsQoS()", {"reliability": "system_default"}),
    ],
)
def test_interpret(raw: str, expected: dict[str, str]) -> None:
    got = interpret_qos(raw)
    assert got["as_written"] == raw
    for key, value in expected.items():
        assert got[key] == value


def test_variable_qos_is_unknown_except_modifiers() -> None:
    got = interpret_qos("durable_qos")
    assert "reliability" not in got
    assert interpret_qos("qos.transient_local()")["durability"] == "transient_local"


def test_incompatibility_rules() -> None:
    best = interpret_qos("rclcpp::SensorDataQoS()")
    rel = interpret_qos("rclcpp::QoS{1}.transient_local()")
    assert incompatible(best, rel) == [
        "publisher best_effort, subscriber requires reliable",
        "publisher volatile, subscriber requires transient_local",
    ]
    assert incompatible(rel, best) == []
