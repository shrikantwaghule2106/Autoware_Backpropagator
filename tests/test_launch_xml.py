from pathlib import Path

import pytest

from adviewer.ingest.launch_xml import LaunchContext, substitute
from adviewer.ingest.package_xml import index_packages
from adviewer.model import Graph

MINI = Path(__file__).parent / "fixtures" / "mini_autoware"


@pytest.fixture
def ctx() -> LaunchContext:
    return LaunchContext(index_packages(MINI), MINI)


def test_nested_var_inside_find_pkg_share(ctx: LaunchContext) -> None:
    graph = Graph()
    out = substitute("$(find-pkg-share $(var pkg))/launch/x.xml", {"pkg": "mrm_handler"}, ctx, graph)
    assert out == str(MINI / "system" / "mrm_handler") + "/launch/x.xml"
    assert graph.meta.warnings == []


def test_var_concatenated_inside_find_pkg_share(ctx: LaunchContext) -> None:
    out = substitute("$(find-pkg-share $(var name)_handler)", {"name": "mrm"}, ctx, Graph())
    assert out == str(MINI / "system" / "mrm_handler")


def test_unknown_inner_var_stays_unresolved_without_bogus_warning(ctx: LaunchContext) -> None:
    graph = Graph()
    text = "$(find-pkg-share $(var missing))/x.xml"
    assert substitute(text, {}, ctx, graph) == text
    assert graph.meta.warnings == []


def test_self_referencing_value_does_not_grow(ctx: LaunchContext) -> None:
    # results are inserted once, never expanded again (a runaway OOM'd the real-tree run)
    vars_ = {"x": "$(var x)/a"}
    assert substitute("$(var x)", vars_, ctx, Graph()) == "$(var x)/a"


def test_parentheses_inside_eval(ctx: LaunchContext) -> None:
    assert substitute("$(eval \"('$(var a)' == 'b') or False\")", {"a": "b"}, ctx, Graph()) == "True"


def test_backslash_escape_in_literal_text(ctx: LaunchContext) -> None:
    # Autoware pose_twist_estimator.launch.xml:12: value="[\\'ndt\\',\\'yabloc\\']"
    assert substitute("[\\'ndt\\',\\'yabloc\\']", {}, ctx, Graph()) == "['ndt','yabloc']"
