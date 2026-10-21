"""YAML launch files (F3), unscoped includes and repeated includes."""
from pathlib import Path

import pytest

from adviewer.ingest.launch_xml import LaunchContext, parse_launch_file
from adviewer.ingest.package_xml import index_packages
from adviewer.model import Direction, Graph

FIX = Path(__file__).parent / "fixtures" / "launch_yaml"
TOP = FIX / "yaml_top" / "launch" / "top.launch.xml"


@pytest.fixture(scope="module")
def graph() -> Graph:
    g = Graph()
    parse_launch_file(TOP, g, LaunchContext(index_packages(FIX), FIX))
    return g


def test_yaml_node_with_remaps(graph: Graph) -> None:
    assert "/control/checker" in graph.nodes  # push-ros-namespace inside a YAML group
    subs = {e.node for e in graph.subscribers("/planning/trajectory")}
    pubs = {e.node for e in graph.publishers("/control/checker/status")}
    assert subs == {"/control/checker"}
    assert pubs == {"/control/checker"}


def test_args_of_included_yaml_reach_the_includer(graph: Graph) -> None:
    # ROS 2 Jazzy includes are unscoped: the preset's arg defaults are visible afterwards
    params = graph.nodes["/user"].params
    assert params["flag"] == "true"  # YAML bool written back as launch text
    assert params["mode"] == "fast"


def test_file_included_twice_is_parsed_twice(graph: Graph) -> None:
    assert "/top_driver" in graph.nodes and "/left_driver" in graph.nodes
    assert {e.topic for e in graph.edges if e.direction == Direction.publish} >= {
        "/sensing/top/points", "/sensing/left/points"
    }


def test_include_cycle_and_bad_yaml_warn(graph: Graph) -> None:
    assert any("include cycle" in w for w in graph.meta.warnings)
    assert any("broken.yaml" in w and "top-level 'launch:'" in w for w in graph.meta.warnings)
