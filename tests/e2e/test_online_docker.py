"""Online mode end to end, the way users meet it: real containers, the real probe, real DDS.
Each case is a bug or a setup found by hand before (D32–D34), kept here so CI finds it next time."""
import json
import os
import random
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

from adviewer.model import Graph

ADVIEWER = [str(Path(sys.executable).with_name("adviewer"))]
IMAGE = "adviewer-probe:dev"
LOOPBACK_CYCLONE = """<?xml version="1.0" encoding="UTF-8"?>
<CycloneDDS xmlns="https://cdds.io/config"><Domain Id="any"><General>
<Interfaces><NetworkInterface name="lo" priority="default" multicast="false"/></Interfaces>
<AllowMulticast>false</AllowMulticast></General>
<Discovery><ParticipantIndex>auto</ParticipantIndex><MaxAutoParticipantIndex>32</MaxAutoParticipantIndex>
<Peers><Peer address="localhost"/></Peers></Discovery></Domain></CycloneDDS>
"""


def sh(*cmd: str, check: bool = True, timeout: float = 600, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(cmd), capture_output=True, text=True, check=check, timeout=timeout, env=env)


@pytest.fixture(scope="session")
def probe_image() -> str:
    sh(*ADVIEWER, "probe", "build", timeout=1800)
    return IMAGE


@pytest.fixture
def names() -> list[str]:
    created: list[str] = []
    yield created
    for name in created:
        sh("docker", "rm", "-f", name, check=False)
    for net in [n for n in created if n.startswith("net-")]:
        sh("docker", "network", "rm", net, check=False)


def publisher(name: str, names: list[str], *run_args: str, topic: str = "/sensing/lidar/points", rate: int = 20,
              image: str = IMAGE) -> None:
    names.append(name)
    sh("docker", "run", "-d", "--rm", "--name", name, *run_args, "--entrypoint", "bash", image, "-c",
       f'source /opt/ros/$ROS_DISTRO/setup.bash && ros2 topic pub -r {rate} {topic} std_msgs/msg/String "{{data: x}}"')
    time.sleep(4)


def probe_once(out: Path, *args: str) -> dict:
    sh(*ADVIEWER, "probe", "run", "--out", str(out), "--no-loop", "--window", "3", *args, timeout=900)
    return {t["name"]: t for t in json.loads(out.read_text(encoding="utf-8"))["topics"]}


def domain() -> str:
    return str(random.randint(20, 200))


def test_default_probe_measures_a_cyclone_publisher(tmp_path: Path, names: list[str], probe_image: str) -> None:
    d = domain()
    publisher(f"pub-{d}", names, "--network", "host", "-e", "RMW_IMPLEMENTATION=rmw_cyclonedds_cpp", "-e", f"ROS_DOMAIN_ID={d}")
    topics = probe_once(tmp_path / "probe_dump.json", "--domain", d)
    assert topics["/sensing/lidar/points"]["measured"] and topics["/sensing/lidar/points"]["rate_hz"] > 15
    assert (tmp_path / "probe_dump.json").stat().st_uid == os.getuid()  # the files are the user's


def test_like_joins_a_separate_network_with_a_loopback_only_cyclone_file(tmp_path: Path, names: list[str], probe_image: str) -> None:
    d = domain()
    (tmp_path / "cfg").mkdir()
    (tmp_path / "cfg" / "cyclonedds.xml").write_text(LOOPBACK_CYCLONE, encoding="utf-8")
    net = f"net-{d}"
    names.append(net)
    sh("docker", "network", "create", net)
    stack = f"aw-{d}"
    publisher(stack, names, "--network", net, "--ipc", "shareable", "-v", f"{tmp_path / 'cfg'}:/cfg:ro",
              "-e", "RMW_IMPLEMENTATION=rmw_cyclonedds_cpp", "-e", f"ROS_DOMAIN_ID={d}", "-e", "CYCLONEDDS_URI=file:///cfg/cyclonedds.xml")
    assert probe_once(tmp_path / "a" / "probe_dump.json", "--domain", d) == {}  # the default probe sees nothing there
    topics = probe_once(tmp_path / "b" / "probe_dump.json", "--like", stack)
    assert topics["/sensing/lidar/points"]["rate_hz"] > 15


def test_like_follows_its_container_through_a_restart(tmp_path: Path, names: list[str], probe_image: str) -> None:
    """A restart gives the container a new network namespace: the probe stayed in the old one and
    reported 0 Hz for everything (found in the plugin review, 2026-10-21)."""
    d = domain()
    net, stack = f"net-{d}", f"rs-{d}"
    names += [stack, net]
    sh("docker", "network", "create", net)
    sh("docker", "run", "-d", "--name", stack, "--network", net, "-e", "RMW_IMPLEMENTATION=rmw_cyclonedds_cpp",
       "-e", f"ROS_DOMAIN_ID={d}", "--entrypoint", "bash", probe_image, "-c",
       'source /opt/ros/$ROS_DISTRO/setup.bash && ros2 topic pub -r 20 /sensing/lidar/points std_msgs/msg/String "{data: x}"')
    dump, log = tmp_path / "probe_dump.json", tmp_path / "probe.log"

    def rate() -> float:
        try:
            topics = json.loads(dump.read_text(encoding="utf-8"))["topics"]
        except (OSError, ValueError, KeyError):
            return 0.0
        return next((t.get("rate_hz") or 0.0 for t in topics if t["name"] == "/sensing/lidar/points"), 0.0)

    with log.open("w", encoding="utf-8") as out:
        probe = subprocess.Popen([*ADVIEWER, "probe", "run", "--like", stack, "--out", str(dump), "--window", "2", "--no-record"],
                                 stdout=out, stderr=subprocess.STDOUT)
    try:
        for _ in range(90):
            if rate() > 15:
                break
            time.sleep(1)
        assert rate() > 15
        sh("docker", "restart", "-t", "1", stack)
        restarted = time.time()
        for _ in range(90):
            time.sleep(1)
            if "joins it again" in log.read_text(encoding="utf-8") and dump.stat().st_mtime > restarted + 3 and rate() > 15:
                break
        assert "joins it again" in log.read_text(encoding="utf-8"), log.read_text(encoding="utf-8")[-2000:]
        assert dump.stat().st_mtime > restarted + 3 and rate() > 15  # measuring the restarted container
    finally:
        probe.send_signal(signal.SIGTERM)
        probe.wait(timeout=30)
    assert sh("docker", "ps", "-q", "--filter", "name=^adviewer-probe-[0-9]").stdout.strip() == ""


def test_like_with_fast_dds_receives_data(tmp_path: Path, names: list[str], probe_image: str) -> None:
    """ROS 2's default RMW: discovery worked but shared memory delivered nothing across containers."""
    d = domain()
    stack = f"fast-{d}"
    publisher(stack, names, "-e", f"ROS_DOMAIN_ID={d}")  # RMW unset: Fast DDS, private IPC, root
    topics = probe_once(tmp_path / "probe_dump.json", "--like", stack)
    assert topics["/sensing/lidar/points"]["rate_hz"] > 15


def test_server_records_the_history_and_probe_stops_with_adviewer(tmp_path: Path, names: list[str], probe_image: str) -> None:
    d = domain()
    publisher(f"rec-{d}", names, "--network", "host", "-e", "RMW_IMPLEMENTATION=rmw_cyclonedds_cpp", "-e", f"ROS_DOMAIN_ID={d}")
    dump = tmp_path / "probe_dump.json"
    graph = tmp_path / "graph.json"
    Graph().save(graph)
    probe = subprocess.Popen([*ADVIEWER, "probe", "run", "--out", str(dump), "--window", "2", "--domain", d, "--no-record"])
    port = str(random.randint(18800, 18999))
    server = subprocess.Popen([*ADVIEWER, "serve", str(graph), "--live", str(dump), "--port", port])
    try:
        body: dict = {}
        for _ in range(60):
            time.sleep(1)
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/timeline?last=60", timeout=5) as r:
                    body = json.loads(r.read())
            except OSError:
                continue
            if body.get("available") and body["summary"]["windows"] >= 3:
                break
        assert body.get("available") and body["summary"]["windows"] >= 3  # recorded by the server alone
    finally:
        server.terminate()
        probe.send_signal(signal.SIGTERM)  # like stopping a VS Code task
        probe.wait(timeout=30)
    left = sh("docker", "ps", "-q", "--filter", "name=^adviewer-probe-[0-9]").stdout.strip()
    assert left == ""  # the probe container stopped with adviewer


@pytest.mark.skipif(not os.environ.get("ADVIEWER_E2E_DISTRO"), reason="set ADVIEWER_E2E_DISTRO (e.g. humble) to test another ROS 2")
def test_probe_on_another_ros_distribution(tmp_path: Path, names: list[str]) -> None:
    distro = os.environ["ADVIEWER_E2E_DISTRO"]
    image = f"adviewer-probe:{distro}"
    sh(*ADVIEWER, "probe", "build", "--base", f"ros:{distro}", "--image", image, timeout=1800)
    d = domain()
    publisher(f"dist-{d}", names, "--network", "host", "-e", "RMW_IMPLEMENTATION=rmw_cyclonedds_cpp", "-e", f"ROS_DOMAIN_ID={d}", image=image)
    topics = probe_once(tmp_path / "probe_dump.json", "--image", image, "--domain", d)
    assert topics["/sensing/lidar/points"]["rate_hz"] > 15
