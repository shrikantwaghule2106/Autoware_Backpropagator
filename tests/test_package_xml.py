from pathlib import Path

from adviewer.ingest.package_xml import index_packages
from adviewer.model import Graph

FIX = Path(__file__).parent / "fixtures" / "mini_install"


def test_colcon_ignore_at_root_is_not_honoured() -> None:
    packages = index_packages(FIX)
    assert "pkg_a" in packages


def test_colcon_ignore_below_root_still_skips_package() -> None:
    packages = index_packages(FIX)
    assert "pkg_b" not in packages

def test_broken_package_xml_warns_instead_of_disappearing() -> None:
    graph = Graph()
    packages = index_packages(FIX, graph)
    assert "pkg_c" not in packages
    assert any("package.xml parse error:" in w for w in graph.meta.warnings)
