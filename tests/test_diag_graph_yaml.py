from pathlib import Path

from adviewer.ingest.diag_graph_yaml import _substitute, parse_diag_graph_file
from adviewer.ingest.launch_xml import LaunchContext
from adviewer.ingest.package_xml import index_packages
from adviewer.model import Graph

FIX = Path(__file__).parent / "fixtures" / "diag_invalid"
SUBST_FIX = Path(__file__).parent / "fixtures" / "diag_subst"
MINI = Path(__file__).parent / "fixtures" / "mini_autoware"




def test_units_not_a_list_warns_instead_of_crashing() -> None:
    graph = Graph()
    parse_diag_graph_file(FIX / "units_not_list.yaml", graph)
    assert graph.diag_units == {}
    assert any("should be a list" in w for w in graph.meta.warnings)


def test_list_not_a_list_keeps_unit_without_children() -> None:
    graph = Graph()
    parse_diag_graph_file(FIX / "list_not_list.yaml", graph)
    assert graph.diag_units["/a"].children == []
    assert any("should be a list" in w for w in graph.meta.warnings)


def test_items_not_mappings_are_skipped_with_warning() -> None:
    graph = Graph()
    parse_diag_graph_file(FIX / "items_not_mapping.yaml", graph)
    assert graph.diag_units["/b"].children == []
    warnings = [w for w in graph.meta.warnings if "should be a mapping" in w]
    assert len(warnings) == 2

def test_top_level_not_a_mapping_warns() -> None:
    graph = Graph()
    parse_diag_graph_file(FIX / "top_not_mapping.yaml", graph)
    assert graph.diag_units == {}
    assert any("top level should be a mapping" in w for w in graph.meta.warnings)

def test_dirname_loads_sibling_file() -> None:
    graph = Graph()
    parse_diag_graph_file(SUBST_FIX / "main.yaml", graph)
    assert "/from_sub" in graph.diag_units
    assert graph.meta.warnings == []


def test_unresolvable_substitution_warns() -> None:
    graph = Graph()
    parse_diag_graph_file(SUBST_FIX / "unsupported.yaml", graph)
    assert any("cannot resolve" in w for w in graph.meta.warnings)

def test_find_pkg_share_resolves_known_package() -> None:
    ctx = LaunchContext(index_packages(MINI), MINI)
    result = _substitute("$(find-pkg-share diag_agg)/config/sub.yaml", MINI / "x.yaml", ctx, Graph(), "test")
    assert result is not None  # Windows gives "D:\…\diag_agg/config/sub.yaml": compare as paths
    assert Path(result) == MINI / "system" / "diag_agg" / "config" / "sub.yaml"


def test_find_pkg_share_unknown_package_warns() -> None:
    graph = Graph()
    ctx = LaunchContext(index_packages(MINI), MINI)
    assert _substitute("$(find-pkg-share nope)/x.yaml", MINI / "x.yaml", ctx, graph, "test") is None
    assert any("cannot resolve" in w for w in graph.meta.warnings)


SELECT = Path(__file__).parent / "fixtures" / "diag_select"


def test_only_the_launched_config_is_read() -> None:
    from adviewer.ingest import ingest_autoware_tree

    g = ingest_autoware_tree(SELECT, entry_launch=str(SELECT / "cfg/launch/top.launch.xml"))
    assert "/autoware/carla_only" not in g.diag_units  # alt.yaml not merged (F9)
    assert g.diag_units["/autoware/modes/autonomous"].type == "and"
    loc = g.diag_units["/autoware/localization"]
    assert loc.diag_name == "topic_state_monitor_pose: localization_topic_status"  # loader.cpp:181
    assert "/autoware/obsolete" not in g.diag_units  # type: remove applied
    assert "/autoware/obsolete" not in g.diag_units["/autoware/modes/autonomous"].children


def test_without_launched_aggregator_all_configs_merge_with_warning() -> None:
    from adviewer.ingest import ingest_autoware_tree

    g = ingest_autoware_tree(SELECT / "cfg")  # a tree without the aggregator package
    assert "/autoware/carla_only" in g.diag_units
    assert any("all configs merged" in w for w in g.meta.warnings)
