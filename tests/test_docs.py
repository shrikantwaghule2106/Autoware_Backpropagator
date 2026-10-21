"""Topic and node descriptions from Autoware's own READMEs and .msg files (topic docs)."""
from pathlib import Path

from adviewer.analysis.details import topic_about
from adviewer.ingest.package_xml import PackageInfo
from adviewer.ingest.readme_docs import apply_readme_docs, parse_readme
from adviewer.model import Confidence, Direction, Edge, Graph, Layer, Node

README = """# mrm_handler

## Purpose

MRM Handler is a node to select a proper MRM
from a system failure state.

## Inputs / Outputs

### Input

| Name                       | Type                         | Description                     |
| -------------------------- | ---------------------------- | ------------------------------- |
| `~/input/odometry`         | `nav_msgs::msg::Odometry`    | Used to decide whether stopped  |

### Output

| Name                          | Type                                   | Description                  |
| ----------------------------- | -------------------------------------- | ---------------------------- |
| `/system/fail_safe/mrm_state` | `autoware_adapi_v1_msgs::msg::MrmState` | Inform MRM execution state   |
| `/system/mrm/operate`         | `tier4_system_msgs::srv::OperateMrm`   | a service, not a topic       |
"""


def test_parse_readme() -> None:
    purpose, rows = parse_readme(README)
    assert purpose == ("MRM Handler is a node to select a proper MRM from a system failure state.", 5)
    assert [(r.name, r.role, r.line) for r in rows] == [
        ("~/input/odometry", "input", 14), ("/system/fail_safe/mrm_state", "output", 20)]


def test_docs_attached_through_remaps(tmp_path: Path) -> None:
    pkg = tmp_path / "autoware_mrm_handler"
    pkg.mkdir()
    (pkg / "README.md").write_text(README)
    g = Graph()
    g.add_node(Node(id="/system/mrm_handler", package="autoware_mrm_handler", layer=Layer.system))
    g.add_edge(Edge(node="/system/mrm_handler", topic="/localization/kinematic_state",
                    direction=Direction.subscribe, confidence=Confidence.static_source,
                    evidence="a.launch.xml:3: remap from='~/input/odometry' to='/localization/kinematic_state'"))
    g.add_edge(Edge(node="/system/mrm_handler", topic="/system/fail_safe/mrm_state",
                    direction=Direction.publish, confidence=Confidence.static_source, evidence="a.launch.xml:4"))
    info = PackageInfo("autoware_mrm_handler", pkg, Layer.system, "system/autoware_mrm_handler")
    assert apply_readme_docs(g, {"autoware_mrm_handler": info}) == 2
    [doc] = g.topics["/localization/kinematic_state"].docs  # matched via the remap's from-name
    assert doc.text == "Used to decide whether stopped" and doc.source == "system/autoware_mrm_handler/README.md:14"
    assert g.nodes["/system/mrm_handler"].docs[0].role == "purpose"
    about = topic_about(g, "/system/fail_safe/mrm_state")
    assert about is not None and about["docs"][0]["text"] == "Inform MRM execution state"
    assert about["role"][0].startswith("Produced by 1 node(s) in system")
    ext = topic_about(g, "/localization/kinematic_state")
    assert ext is not None and "has to provide it" in ext["role"][0]  # an external input
