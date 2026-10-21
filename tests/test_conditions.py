"""Launch if=/unless= and $(eval) (F10)."""
from pathlib import Path

import pytest

from adviewer.ingest.conditions import eval_expression, evaluate_condition
from adviewer.ingest.launch_xml import LaunchContext, parse_launch_file
from adviewer.ingest.package_xml import index_packages
from adviewer.model import Graph

FIX = Path(__file__).parent / "fixtures" / "conditions"
LAUNCH = FIX / "cond_pkg" / "launch"


@pytest.mark.parametrize(
    ("expr", "result"),
    [
        ("'a' == 'a'", "True"),
        ("'a'=='b' and 'c'=='c'", "False"),
        ('"x" != "y" or 1 == 2', "True"),
        ("6>=8", "False"),
        ("not 'true' == 'false'", "True"),
        ("'a' and 'b'", "b"),  # Python returns the operand; not a valid condition in ROS
        ("__import__('os').system('true')", None),  # never executed
        ("'$(var x)' == 'y'", None),  # unresolved inner substitution
        ("'3' >= 2", None),  # TypeError in Python, an error in ROS
        ("'centerpoint/x'.split('/')[0]", "centerpoint"),  # Autoware perception.launch.xml:51
        ("len(['ndt', 'yabloc'])> 1", "True"),
        ("'ndt' in 'ndt_yabloc'.split('_')", "True"),
        ("'a'.format()", None),  # method not whitelisted
        # Autoware pose_twist_estimator.launch.xml:13-14
        ("list(set('ndt'.split('_')).intersection(['ndt','yabloc','eagleye']))", "['ndt']"),
        ("len(open('x'))", None),
    ],
)
def test_eval_expression(expr: str, result: str | None) -> None:
    assert eval_expression(expr) == result


@pytest.mark.parametrize(
    ("text", "value"), [("True", True), (" 1 ", True), ("FALSE", False), ("0", False), ("yes", None), ("", None)]
)
def test_condition_truth_rule(text: str, value: bool | None) -> None:
    assert evaluate_condition(text) is value


def _parse(name: str) -> Graph:
    g = Graph()
    parse_launch_file(LAUNCH / name, g, LaunchContext(index_packages(FIX), FIX))
    return g


def test_xml_conditions() -> None:
    g = _parse("cond.launch.xml")
    assert "/planner_a" in g.nodes and "/planner_b" not in g.nodes
    assert "/sensing/gnss/fix_x" in g.topics and "/sensing/gnss/fix_y" not in g.topics
    assert "/debug" not in g.topics  # remap with if="false"
    assert "/camera5" in g.nodes and "/camera8" not in g.nodes
    assert "/undecided" in g.nodes  # kept …
    assert any("undecided" not in w and "not_declared" in w and "element kept" in w
               for w in g.meta.warnings)  # … and reported
    assert "/envnode" in g.nodes  # $(env X default) takes the default
    assert not any("never.launch.xml" in w for w in g.meta.warnings)  # include not attempted


def test_python_conditions() -> None:
    g = _parse("cond.launch.py")
    assert "/py_a" not in g.nodes and "/py_b" in g.nodes
    assert "/py_fast" in g.nodes and "/py_expr" not in g.nodes
    assert "/py_in_container" not in g.nodes  # the container's condition is false
    assert "/py_grouped" not in g.nodes
