"""The commands a user types: every one starts and explains itself; the offline ones work end to
end on a fixture; the online ones fail with a message, not a traceback, when nothing is set up."""
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from adviewer import __version__
from adviewer.analysis.switches import list_switches
from adviewer.cli import app
from adviewer.ingest import ingest_autoware_tree

runner = CliRunner()
FIX = Path(__file__).parent / "fixtures"
MINI = FIX / "mini_autoware"
TEMPLATE = Path(__file__).resolve().parents[1] / "src" / "adviewer" / "data" / "target.example.yaml"
COMMANDS = [[c] for c in ("ingest", "layer-table", "serve", "init", "extract", "fetch-source", "up", "record", "map",
                          "doctor", "warnings", "external", "annotate", "compare")] + \
           [["probe", c] for c in ("build", "run")]


@pytest.mark.parametrize("cmd", COMMANDS, ids=[" ".join(c) for c in COMMANDS])
def test_every_command_explains_itself(cmd: list[str]) -> None:
    r = runner.invoke(app, [*cmd, "--help"])
    assert r.exit_code == 0 and "Usage" in r.output, r.output


def test_version() -> None:
    r = runner.invoke(app, ["--version"])
    assert r.exit_code == 0 and r.output.strip() == f"adviewer {__version__}"


def _dump(path: Path, rate: float, extra: bool = False) -> Path:
    topics = [{"name": "/sensing/lidar/points", "types": ["sensor_msgs/msg/PointCloud2"], "measured": True, "rate_hz": rate,
               "count": int(rate * 5), "bandwidth_Bps": 1e6, "publishers": [{"node": "/lidar", "type": "x", "qos": {}}]}]
    if extra:
        topics.append({"name": "/sensing/camera/image", "types": ["sensor_msgs/msg/Image"], "measured": True, "rate_hz": 30.0,
                       "count": 150, "bandwidth_Bps": 6e7, "publishers": [{"node": "/camera", "type": "x", "qos": {}}]})
    path.write_text(json.dumps({"captured_at": "2026-10-20T10:00:00Z", "window_s": 5.0, "nodes": ["/lidar"], "topics": topics}),
                    encoding="utf-8")
    return path


def test_offline_commands_end_to_end(tmp_path: Path) -> None:
    graph = tmp_path / "graph.json"
    r = runner.invoke(app, ["ingest", "--autoware-root", str(MINI), "--entry-launch", str(MINI / "launch/top/launch/autoware.launch.xml"),
                            "--out", str(graph)])
    assert r.exit_code == 0 and "nodes" in r.output and graph.is_file(), r.output
    for cmd in (["warnings", str(graph)], ["external", str(graph)]):
        assert runner.invoke(app, cmd).exit_code == 0
    r = runner.invoke(app, ["map", str(graph), str(TEMPLATE), "--markdown", str(tmp_path / "check.md")])
    assert r.exit_code == 0 and "coverage" in r.output and (tmp_path / "check.md").is_file(), r.output


def test_live_commands_on_dumps(tmp_path: Path) -> None:
    graph = tmp_path / "graph.json"
    runner.invoke(app, ["ingest", "--autoware-root", str(MINI), "--out", str(graph)])
    base, loaded = _dump(tmp_path / "base.json", 10.0), _dump(tmp_path / "loaded.json", 6.0, extra=True)
    r = runner.invoke(app, ["annotate", str(graph), str(base), "--out", str(tmp_path / "live.json")])
    assert r.exit_code == 0 and (tmp_path / "live.json").is_file(), r.output
    r = runner.invoke(app, ["compare", str(base), str(loaded)])
    assert r.exit_code == 0 and "1 topic(s) dropped" in r.output, r.output


def test_online_commands_fail_with_a_message(tmp_path: Path) -> None:
    r = runner.invoke(app, ["probe", "run", "--native", "--workspace", str(tmp_path / "ws"), "--out", str(tmp_path / "d.json")])
    assert r.exit_code == 2 and "probe build --native" in r.output, r.output
    r = runner.invoke(app, ["probe", "run", "--native", "--like", "x", "--out", str(tmp_path / "d.json")])
    assert r.exit_code == 2 and "cannot be combined" in r.output


def test_switches_list_with_commands() -> None:
    g = ingest_autoware_tree(FIX / "switches", entry_launch=str(FIX / "switches/sw_pkg/launch/top.launch.xml"))
    rows = {s["name"]: s for s in list_switches(g)}
    per = rows["launch_perception"]
    assert per["boolean"] and per["node_count"] == 2 and per["command"].endswith("launch_perception:=false")
    order = [s["name"] for s in list_switches(g)]
    assert order.index("launch_perception") < order.index("launch_radar")  # the switch that starts more nodes first


def test_like_probe_joins_its_container_again_after_a_restart(monkeypatch, tmp_path: Path) -> None:
    import sys

    import typer

    from adviewer import cli, probe_launch

    # started, still the same, stopped (compose down / restart), still stopped, back as a new run
    runs = ["a 1", "a 1", "", "", "b 2"]
    monkeypatch.setattr(probe_launch, "container_instance", lambda name: runs.pop(0) if len(runs) > 1 else runs[0])
    removed: list[str] = []
    monkeypatch.setattr(probe_launch, "remove_container", removed.append)
    marker = tmp_path / "second-run"
    again = [sys.executable, "-c", f"open({str(marker)!r}, 'w').write('ran')"]
    follow = cli._Follow("aw", lambda: again, "adviewer-probe-1", poll=0.05)
    blind = [sys.executable, "-c", "import time; time.sleep(60)"]  # the probe left in the old network
    with pytest.raises(typer.Exit) as done:
        cli._run_with_history(tmp_path / "d.json", blind, False, follow=follow)  # containers: tests/e2e
    assert done.value.exit_code == 0 and marker.read_text() == "ran"
    assert removed == ["adviewer-probe-1"]  # the old probe's container is gone before it joins again
