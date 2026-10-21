"""Layer assignment (F8): path rules, release table for install spaces, include layers."""
from pathlib import Path

import pytest

from adviewer.ingest import ingest_autoware_tree
from adviewer.ingest.package_xml import index_packages, layer_from_path
from adviewer.ingest.release_data import apply_layers, detect_release
from adviewer.model import Graph, Layer

FIX = Path(__file__).parent / "fixtures" / "layers_install"


@pytest.mark.parametrize(
    ("rel", "name", "layer"),
    [
        ("universe/autoware_universe/evaluator/autoware_control_evaluator", "", Layer.evaluator),
        ("universe/autoware_universe/simulator/autoware_fault_injection", "", Layer.simulator),
        ("universe/autoware_universe/visualization/tier4_state_rviz_plugin", "", Layer.visualization),
        ("core/autoware_core/api/autoware_default_adapi", "", Layer.api),
        ("launcher/autoware_launch/sensor_kit/sample_sensor_kit_launch/x", "", Layer.sensing),
        ("launcher/autoware_launch/tier4_universe_launch/tier4_control_launch",
         "tier4_control_launch", Layer.control),
        ("x/tier4_autoware_api_launch", "tier4_autoware_api_launch", Layer.api),
        ("launcher/autoware_launch/autoware_launch", "autoware_launch", Layer.launch),
        ("some/where/else", "", Layer.unknown),
    ],
)
def test_layer_from_path(rel: str, name: str, layer: Layer) -> None:
    assert layer_from_path(rel, name) == layer


def test_release_detected_and_table_applied() -> None:
    packages = index_packages(FIX)
    release = detect_release(packages)
    assert release is not None and release.release == "1.9.0"
    apply_layers(packages, None, release)
    assert packages["autoware_mrm_handler"].layer == Layer.system  # generated table
    assert packages["nebula_ros"].layer == Layer.sensing  # name rule nebula*
    assert packages["my_private_pkg"].layer == Layer.unknown


def test_install_space_nodes_get_package_layers_not_launch() -> None:
    g: Graph = ingest_autoware_tree(
        FIX, entry_launch=str(FIX / "autoware_launch/share/autoware_launch/launch/autoware.launch.xml")
    )
    assert g.meta.autoware_release == "1.9.0"
    assert g.nodes["/mrm_handler"].layer == Layer.system
    assert g.nodes["/nebula"].layer == Layer.sensing
    assert g.nodes["/driver"].layer == Layer.sensing  # from tier4_sensing_launch, not inherited
    assert g.nodes["/top_node"].layer == Layer.launch
