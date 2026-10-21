"""Expected rates with their source (FR-6)."""
from adviewer.ingest.cpp_source import Timer, scan_timers
from adviewer.ingest.rates import apply_rates
from adviewer.model import Graph, Node, Param, ParamKind, Topic

CPP = """timer_ = create_wall_timer(100ms, cb);
x = rclcpp::create_timer(
   this, get_clock(),
   std::chrono::milliseconds(50), [this]() {});
y = this->create_wall_timer(std::chrono::duration<double>(0.2), cb);
z = create_wall_timer(period_ns, cb);
rclcpp::Rate(20).period();
"""


def test_literal_timer_periods() -> None:
    got = [(round(t.hz, 3), t.line) for t in scan_timers(CPP)]
    assert got == [(10.0, 1), (20.0, 2), (5.0, 5), (20.0, 7)]  # period_ns skipped


def _graph() -> Graph:
    g = Graph()
    g.add_node(Node(id="/a", package="pkg_a"))
    g.add_node(Node(id="/system/topic_state_monitor_x", package="autoware_topic_state_monitor"))
    g.add_topic(Topic(id="/x"))
    for name, value in [("update_rate", "10.0"), ("steer_rate_lim", "0.5"),
                        ("timer_period_sec", "0.05"), ("publish_period_ms", "200")]:
        g.params.append(Param(node="/a", name=name, value=value, kind=ParamKind.param_file,
                              evidence=f"a.yaml:{len(g.params) + 1}"))
    for name, value in [("topic", "/x"), ("warn_rate", "5.0"), ("error_rate", "1.0"), ("timeout", "1.0")]:
        g.params.append(Param(node="/system/topic_state_monitor_x", name=name, value=value,
                              kind=ParamKind.launch_param, evidence="topics.yaml:7"))
    return g


def test_rates_from_timers_params_and_monitors() -> None:
    g = _graph()
    apply_rates(g, {"pkg_a": [("pkg_a/src/a.cpp", Timer(10.0, 3, "100ms"))]})
    a = {(r.kind, r.hz) for r in g.nodes["/a"].rates}
    assert a == {("timer", 10.0), ("param", 10.0), ("param", 20.0), ("param", 5.0)}  # not steer_rate_lim
    [mon] = g.topics["/x"].rates
    assert mon.kind == "monitor" and mon.hz == 5.0 and mon.evidence == "topics.yaml:7"
    assert "error below 1.0 Hz" in mon.note
