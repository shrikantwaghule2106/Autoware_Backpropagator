"""Switches: launch variables that turn parts of Autoware on or off."""
from pathlib import Path

from adviewer.ingest import ingest_autoware_tree

FIX = Path(__file__).parent / "fixtures" / "switches"


def test_switches_record_what_they_gate() -> None:
    g = ingest_autoware_tree(FIX, entry_launch=str(FIX / "sw_pkg/launch/top.launch.xml"))
    sw = {s.name: s for s in g.switches}
    per = sw["launch_perception"]
    assert per.value == "true" and per.cli_arg == "launch_perception"
    assert per.declared == "sw_pkg/launch/top.launch.xml:2"
    [gate] = per.gates
    assert gate.on is True and gate.tag == "group" and gate.evidence == "sw_pkg/launch/top.launch.xml:4"
    assert set(gate.nodes) == {"/detector", "/tracker"}  # started because of the switch
    radar = sw["launch_radar"].gates[0]
    assert radar.on is False and radar.target == "radar_tracker" and radar.nodes == []
    mon = sw["use_monitor"]
    assert mon.gates[0].on is True and mon.gates[0].nodes == ["/monitor"]  # Python IfCondition
