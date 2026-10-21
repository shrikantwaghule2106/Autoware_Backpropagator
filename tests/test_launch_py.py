from pathlib import Path

from adviewer.ingest.launch_py import parse_launch_py
from adviewer.ingest.launch_xml import LaunchContext
from adviewer.model import Direction, Graph

FIX = Path(__file__).parent / "fixtures" / "launch_py"


def _parse() -> Graph:
    graph = Graph()
    parse_launch_py(FIX / "sample.launch.py", graph, LaunchContext({}, FIX), namespace="/sensing/lidar")
    return graph


def test_literal_composable_node_is_extracted() -> None:
    node = _parse().nodes["/sensing/lidar/crop_box_filter"]
    assert node.package == "demo_filters"
    assert node.executable == "demo::CropBoxFilter"


def test_literal_remaps_become_edges_with_direction() -> None:
    graph = _parse()
    edges = {(e.topic, e.direction) for e in graph.edges if e.node == "/sensing/lidar/crop_box_filter"}
    assert edges == {
        ("/sensing/lidar/pointcloud_raw", Direction.subscribe),
        ("/sensing/lidar/self_cropped/pointcloud", Direction.publish),
    }


def test_evidence_points_to_file_and_line() -> None:
    graph = _parse()
    edge = next(e for e in graph.edges if e.node == "/sensing/lidar/crop_box_filter")
    assert edge.evidence.startswith("sample.launch.py:")


def test_computed_name_is_not_guessed() -> None:
    graph = _parse()
    assert not any("driver" in n for n in graph.nodes)
    assert any("not resolved" in w for w in graph.meta.warnings)


def test_node_namespace_and_absolute_remap() -> None:
    graph = _parse()
    assert "/sensing/lidar/tools/relay" in graph.nodes
    topics = {e.topic for e in graph.edges if e.node == "/sensing/lidar/tools/relay"}
    assert topics == {"/abs/in", "/sensing/lidar/tools/out"}

def _parse_configured(vars_: dict[str, str] | None = None) -> Graph:
    graph = Graph()
    parse_launch_py(FIX / "configured.launch.py", graph, LaunchContext({}, FIX), namespace="/ns", vars_=vars_)
    return graph


def _edges(graph: Graph, node: str) -> set[tuple[str, Direction]]:
    return {(e.topic, e.direction) for e in graph.edges if e.node == node}


def test_defaults_from_declared_arguments_are_used() -> None:
    graph = _parse_configured()
    assert ("/ns/raw", Direction.subscribe) in _edges(graph, "/ns/my_filter")


def test_includer_value_wins_over_default() -> None:
    graph = _parse_configured({"filter_name": "top_filter", "input_topic": "/sensing/lidar/top/points"})
    assert ("/sensing/lidar/top/points", Direction.subscribe) in _edges(graph, "/ns/top_filter")


def test_concatenation_and_list_of_pieces() -> None:
    edges = _edges(_parse_configured(), "/ns/my_filter")
    assert ("/ns/out/filtered", Direction.publish) in edges
    assert ("/ns/out/debug", Direction.publish) in edges


def test_local_variable_assigned_once_is_followed() -> None:  # F11c
    graph = _parse_configured()
    assert any(t.endswith("/raw") and d == Direction.subscribe for t, d in _edges(graph, "/ns/my_filter"))


F11C = FIX / "f11c_pkg" / "launch" / "f11c.launch.py"


def _parse_f11c() -> Graph:
    from adviewer.ingest.package_xml import index_packages

    graph = Graph()
    ctx = LaunchContext(index_packages(FIX), FIX)
    param_path = str(FIX / "f11c_pkg" / "config" / "concat.param.yaml")
    parse_launch_py(F11C, graph, ctx, vars_={"concat_param_path": param_path})
    return graph


def test_parameter_file_through_a_variable() -> None:
    graph = _parse_f11c()
    [p] = [p for p in graph.params if p.node == "/concatenate_data" and p.name == "input_topics"]
    assert p.evidence == "f11c_pkg/config/concat.param.yaml:3"
    assert "/sensing/lidar/top/pointcloud_before_sync" in p.value


def test_fstring_and_path_joins() -> None:
    graph = _parse_f11c()
    assert "/sensing/lidar/concatenated" in graph.topics  # f"{prefix}/concatenated"
    assert any(p.node == "/joined" and p.name == "input_topics" for p in graph.params)
    assert any("missing.yaml" in w and "not found" in w for w in graph.meta.warnings)


def test_twice_assigned_and_loop_variables_are_not_guessed() -> None:
    graph = _parse_f11c()
    assert "/first" not in graph.topics and "/second" not in graph.topics
    assert not any(n.startswith("/looped") for n in graph.nodes)
