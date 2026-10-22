"""Parameter index and where-to-edit (FR-7)."""
from pathlib import Path

import pytest

from adviewer.analysis.where_to_edit import find_params, override_routes
from adviewer.ingest import ingest_autoware_tree
from adviewer.ingest.param_yaml import applies_to
from adviewer.model import Graph, ParamKind

FIX = Path(__file__).parent / "fixtures" / "params"


@pytest.fixture(scope="module")
def graph() -> Graph:
    return ingest_autoware_tree(FIX, entry_launch=str(FIX / "top_pkg/launch/top.launch.xml"))


def test_param_file_leaf_with_line_and_handle(graph: Graph) -> None:
    [p] = [p for p in graph.params if p.name == "max_vel"]  # /other_node's entry not applied
    assert p.node == "/planning/planner" and p.value == "4.17"
    assert p.kind == ParamKind.param_file
    assert p.evidence == "top_pkg/config/common.param.yaml:3"
    assert p.package == "top_pkg"
    # common_param_path is passed explicitly by component.launch.xml, so the argument a
    # user can set on the command line is config_dir
    assert p.via_arg == "config_dir"
    assert "common_param_path = $(var config_dir)/common.param.yaml" in p.via_expr


def test_nested_and_list_values(graph: Graph) -> None:
    [p] = [p for p in graph.params if p.name == "nested.gain"]
    assert p.value == "[1.0, 2.0]"


def test_launch_param_and_args(graph: Graph) -> None:
    [p] = [p for p in graph.params if p.name == "speed_limit"]
    assert p.kind == ParamKind.launch_param and p.value == "5.0" and p.via_arg == "vehicle_speed"
    args = {p.name: p for p in graph.params if p.kind == ParamKind.launch_arg}
    assert args["config_dir"].evidence == "top_pkg/launch/top.launch.xml:2"
    assert "common_param_path" not in args  # its default never takes effect
    assert graph.meta.entry_package == "top_pkg"


def test_routes_source_layout(graph: Graph) -> None:
    [p] = [p for p in graph.params if p.name == "max_vel"]
    routes = override_routes(p, graph)
    assert routes[0]["route"].startswith("launch argument")
    assert "ros2 launch top_pkg top.launch.xml config_dir:=" in routes[0]["how"]
    assert {"route": "source edit",
            "how": "edit top_pkg/config/common.param.yaml:3, then colcon build --packages-select top_pkg"} in routes
    # read from source, Autoware may still run from the official image: where the file is there
    hows = " | ".join(r["how"] for r in routes)
    assert "COPY your_copy /opt/autoware/top_pkg/share/top_pkg/config/common.param.yaml" in hows
    assert "-v $PWD/your_copy:/opt/autoware/top_pkg/share/top_pkg/config/common.param.yaml:ro" in hows


def test_installed_path_of_a_source_file() -> None:
    from adviewer.analysis.where_to_edit import installed_path

    src = "launcher/autoware_launch/autoware_launch/config/planning/common.param.yaml"
    assert installed_path(src, "autoware_launch") == "/opt/autoware/autoware_launch/share/autoware_launch/config/planning/common.param.yaml"
    assert installed_path(src, "other_pkg") == "" and installed_path(src, "") == ""


def test_routes_install_layout(graph: Graph) -> None:
    [p] = [p for p in graph.params if p.name == "max_vel"]
    image = graph.model_copy(deep=True)
    image.meta.layout = "install"
    hows = " | ".join(r["how"] for r in override_routes(p, image))
    assert "COPY your_copy /opt/autoware/top_pkg/config/common.param.yaml" in hows


def test_find_params_groups_and_ranks_exact_first(graph: Graph) -> None:
    hits = find_params(graph, "max_vel")
    assert hits[0]["name"] == "max_vel" and hits[0]["nodes"] == ["/planning/planner"]


@pytest.mark.parametrize(
    ("key", "fqn", "ok"),
    [
        ("/**", "/a/b", True),
        ("b", "/a/b", True),
        ("/a/b", "/a/b", True),
        ("/a/b", "/c/b", False),
        ("/**/b", "/x/y/b", True),
        ("/a/*", "/a/b", True),
        ("/a/*", "/a/b/c", False),
    ],
)
def test_node_key_matching(key: str, fqn: str, ok: bool) -> None:
    assert applies_to(key, fqn) is ok


def test_pass_through_arg_keeps_its_command_line_handle(tmp_path) -> None:
    from adviewer.ingest.launch_xml import LaunchContext, _via_arg, parse_launch_file

    (tmp_path / "inner.launch.xml").write_text('<launch><arg name="x" default="1"/></launch>')
    (tmp_path / "top.launch.xml").write_text(
        f'<launch><arg name="x" default="true"/>'
        f'<include file="{tmp_path}/inner.launch.xml"><arg name="x" value="$(var x)"/></include></launch>')
    ctx, vars_ = LaunchContext({}, tmp_path), {}
    parse_launch_file(tmp_path / "top.launch.xml", Graph(), ctx, vars_)
    assert _via_arg(vars_, "$(var x)")[0] == "x"  # still settable as x:=… on the command line
