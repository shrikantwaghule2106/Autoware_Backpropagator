"""`adviewer probe run` across setups (D32): the docker command for each way of joining Autoware."""
import json
import sys
from pathlib import Path

import pytest

from adviewer import probe_launch
from adviewer.probe_launch import ProbeOptions, ProbeSetupError, build_command, config_paths

FIX = Path(__file__).parent / "fixtures" / "docker"
PROBE_ENV = {"ROS_DISTRO": "jazzy"}
INSTALL = Path("/cache/probe-install-abc")


def _like(**changes) -> dict:
    data = json.loads((FIX / "compose_autoware.json").read_text(encoding="utf-8"))[0]
    for key, value in changes.items():
        section, field = key.split("__")
        data[section][field] = value
    return data


def _opts(**kw) -> ProbeOptions:
    kw.setdefault("user", "1000:1000")
    return ProbeOptions(out=Path("/proj/.adviewer/probe_dump.json"), name="adviewer-probe-1", **kw)


def _env(cmd: list[str]) -> dict[str, str]:
    return dict(cmd[i + 1].split("=", 1) for i, c in enumerate(cmd) if c == "-e")


def test_like_a_compose_container_joins_its_network_ipc_dds_and_image() -> None:
    cmd, notes = build_command(_opts(), "adviewer-probe:dev", PROBE_ENV, like=_like(), install=INSTALL)
    assert cmd[cmd.index("--network") + 1] == "container:stack-autoware-1"  # same interfaces, also loopback
    assert cmd[cmd.index("--ipc") + 1] == "container:stack-autoware-1"
    env = _env(cmd)
    assert env["RMW_IMPLEMENTATION"] == "rmw_cyclonedds_cpp" and env["ROS_DOMAIN_ID"] == "7"
    assert env["CYCLONEDDS_URI"] == "file:///config/dds/cyclonedds.xml" and "PATH" not in env and "DISPLAY" not in env
    assert "/home/user/stack/config:/config:ro" in cmd  # the DDS file comes from the same bind mount
    assert "stack_maps:/maps:ro" not in cmd  # only what the DDS settings need
    assert "ghcr.io/autowarefoundation/autoware:universe-cuda-jazzy-1.9.0" in cmd  # all Autoware's message types
    assert f"{INSTALL.resolve()}:/adviewer/probe:ro" in cmd  # resolved: a drive letter on Windows
    assert cmd[cmd.index("--user") + 1] == "1000:1000"
    script = cmd[-1]
    assert "[ -f /opt/autoware/setup.bash ] && . /opt/autoware/setup.bash" in script
    assert script.endswith('exec "$(ros2 pkg prefix adviewer_probe)/lib/adviewer_probe/probe" --ros-args '
                           "-p output:=/adviewer/out/probe_dump.json -p window:=5.0 -p loop:=true")  # PID 1: stops cleanly
    assert any("like stack-autoware-1" in n for n in notes)


def test_like_a_host_network_container_and_overrides() -> None:
    like = _like(HostConfig__NetworkMode="host", HostConfig__IpcMode="host")
    cmd, _ = build_command(_opts(domain=3, env={"ROS_AUTOMATIC_DISCOVERY_RANGE": "LOCALHOST"}), "adviewer-probe:dev",
                           PROBE_ENV, like=like, install=INSTALL)
    assert cmd[cmd.index("--network") + 1] == "host" and cmd[cmd.index("--ipc") + 1] == "host"
    assert _env(cmd)["ROS_DOMAIN_ID"] == "3" and _env(cmd)["ROS_AUTOMATIC_DISCOVERY_RANGE"] == "LOCALHOST"


def test_like_refuses_what_cannot_work() -> None:
    other = _like()
    other["Config"]["Env"] = [e.replace("ROS_DISTRO=jazzy", "ROS_DISTRO=humble") for e in other["Config"]["Env"]]
    with pytest.raises(ProbeSetupError, match="probe build --base ghcr.io/autowarefoundation/autoware"):
        build_command(_opts(), "adviewer-probe:dev", PROBE_ENV, like=other, install=INSTALL)
    with pytest.raises(ProbeSetupError, match="not running"):
        build_command(_opts(), "adviewer-probe:dev", PROBE_ENV, like=_like(State__Running=False), install=INSTALL)
    with pytest.raises(ProbeSetupError, match="no network"):
        build_command(_opts(), "adviewer-probe:dev", PROBE_ENV, like=_like(HostConfig__NetworkMode="none"), install=INSTALL)


def test_probe_image_default_with_dds_file_and_topic_filter(tmp_path: Path) -> None:
    xml = tmp_path / "cyclonedds.xml"
    xml.write_text("<CycloneDDS/>", encoding="utf-8")
    cmd, _ = build_command(_opts(cyclone_config=xml, include="^/sensing/"), "adviewer-probe:dev", PROBE_ENV)
    env = _env(cmd)
    assert env["RMW_IMPLEMENTATION"] == "rmw_cyclonedds_cpp" and env["ROS_DOMAIN_ID"] == "0"
    assert env["CYCLONEDDS_URI"] == "file:///adviewer/cyclonedds.xml" and f"{xml.resolve()}:/adviewer/cyclonedds.xml:ro" in cmd
    assert env["HOME"] == "/tmp"  # not root: the dump files belong to the user
    assert cmd[cmd.index("--network") + 1] == "host" and "--entrypoint" not in cmd
    at = cmd.index("adviewer-probe:dev")  # the probe image's own entrypoint gets the ROS arguments
    assert cmd[at + 1:at + 4] == ["--ros-args", "-p", "output:=/adviewer/out/probe_dump.json"]
    assert "include:=^/sensing/" in cmd


def test_in_image_and_config_paths() -> None:
    cmd, _ = build_command(_opts(network="stack_default", user=None), "adviewer-probe:dev", PROBE_ENV,
                           in_image="my/autoware:latest", install=INSTALL)
    assert "my/autoware:latest" in cmd and cmd[cmd.index("--network") + 1] == "stack_default" and "--user" not in cmd
    with pytest.raises(ProbeSetupError):
        build_command(_opts(), "adviewer-probe:dev", PROBE_ENV, in_image="my/autoware:latest")
    assert config_paths({"CYCLONEDDS_URI": "file:///a.xml, /b.xml,<CycloneDDS><Domain/></CycloneDDS>",
                         "FASTRTPS_DEFAULT_PROFILES_FILE": "/c.xml"}) == ["/a.xml", "/b.xml", "/c.xml"]


def test_probe_image_must_be_current(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    images = {"old": {"Config": {"Labels": {}, "Env": ["ROS_DISTRO=jazzy"]}},
              "new": {"Config": {"Labels": {"adviewer.probe": "3"}, "Env": ["ROS_DISTRO=jazzy"]}}}
    calls: list[list[str]] = []

    def fake(args: list[str]) -> str:
        calls.append(args)
        if args[:2] == ["image", "inspect"]:
            return "sha256:0123456789abcdef" if "--format" in args else json.dumps([images[args[-1]]])
        if args[0] == "create":
            return "cid42"
        if args[0] == "cp":
            (Path(args[2]) / "setup.bash").parent.mkdir(parents=True)
            (Path(args[2]) / "setup.bash").write_text("", encoding="utf-8")
        return ""

    monkeypatch.setattr(probe_launch, "_docker", fake)
    with pytest.raises(ProbeSetupError, match="run `adviewer probe build` again"):
        probe_launch.probe_image_env("old")
    assert probe_launch.probe_image_env("new") == {"ROS_DISTRO": "jazzy"}
    first = probe_launch.probe_install("new", tmp_path)
    assert first == tmp_path / "probe-install-0123456789ab" and ["rm", "cid42"] in calls
    n = len(calls)
    assert probe_launch.probe_install("new", tmp_path) == first and len(calls) == n + 1  # cached: only the id lookup


@pytest.mark.skipif(sys.platform == "win32", reason="--native runs on Linux: POSIX paths and bash quoting")
def test_native_build_and_run(tmp_path: Path) -> None:
    from adviewer.probe_launch import native_build_script, native_run, native_setups

    have = {Path("/opt/ros/jazzy/setup.bash"), tmp_path / "autoware" / "install" / "setup.bash"}
    found = native_setups([], {"ROS_DISTRO": "jazzy"}, tmp_path, exists=lambda p: p in have)
    assert found == ["/opt/ros/jazzy/setup.bash", str(tmp_path / "autoware" / "install" / "setup.bash")]
    assert native_setups(["/my/setup.bash"], {}, tmp_path) == ["/my/setup.bash"]
    assert native_setups([], {}, tmp_path, exists=lambda p: False) == []
    ws = tmp_path / "ws"
    script = native_build_script(Path("/pkg/cpp"), ws, found)
    assert script.startswith(". /opt/ros/jazzy/setup.bash && . ") and "colcon build --packages-select adviewer_probe" in script
    cmd, env = native_run(_opts(domain=5, include="^/sensing/"), ws, found)
    assert cmd[:2] == ["bash", "-c"] and env == {"ROS_DOMAIN_ID": "5"}  # the rest comes from the user's shell
    assert f". {ws / 'install' / 'setup.bash'}" in cmd[2] and 'exec "$(ros2 pkg prefix adviewer_probe)/lib/adviewer_probe/probe"' in cmd[2]
    assert f"output:={Path('/proj/.adviewer/probe_dump.json').resolve()}" in cmd[2] and "include:=^/sensing/" in cmd[2]


def test_native_env_leaves_out_adviewers_virtualenv() -> None:
    from adviewer.probe_launch import native_env

    env = {"PATH": "/proj/.venv/bin:/usr/local/bin:/usr/bin", "VIRTUAL_ENV": "/proj/.venv",
           "PYTHONPATH": "/proj/.venv/lib/python3.12/site-packages:/opt/ros/jazzy/lib/python3.12/site-packages",
           "ROS_DOMAIN_ID": "42"}
    clean = native_env(env, "/proj/.venv")
    assert clean["PATH"] == "/usr/local/bin:/usr/bin" and "VIRTUAL_ENV" not in clean
    assert clean["PYTHONPATH"] == "/opt/ros/jazzy/lib/python3.12/site-packages" and clean["ROS_DOMAIN_ID"] == "42"
    assert native_env(env, "") == env  # not in a virtualenv: unchanged


def test_selinux_hosts_and_install_space_detection(tmp_path: Path) -> None:
    from adviewer import project
    from adviewer.probe_launch import docker_run_argv, plan_probe, selinux_enforcing

    flag = tmp_path / "enforce"
    flag.write_text("1\n", encoding="utf-8")
    assert selinux_enforcing(flag) and not selinux_enforcing(tmp_path / "missing")
    plan = plan_probe(_opts(selinux=True), "adviewer-probe:dev", PROBE_ENV, like=_like(), install=INSTALL)
    argv = docker_run_argv(plan)
    assert argv[argv.index("--security-opt") + 1] == "label=disable"  # bind mounts readable on SELinux hosts
    cmd = project.detect_prefix_command("img", "/opt/autoware")
    assert cmd[:4] == ["docker", "run", "--rm", "--entrypoint"] and cmd[-1].startswith("for d in /opt/autoware /autoware/install")


def test_fast_dds_probe_uses_udp_unless_the_setup_has_its_own_profile() -> None:
    from adviewer.probe_launch import FASTDDS_UDP, plan_probe

    fast = _like()
    fast["Config"]["Env"] = ["ROS_DISTRO=jazzy", "ROS_DOMAIN_ID=7"]  # RMW unset: ROS 2's default, Fast DDS
    plan = plan_probe(_opts(), "adviewer-probe:dev", PROBE_ENV, like=fast, install=INSTALL)
    assert plan.env["FASTRTPS_DEFAULT_PROFILES_FILE"] == "/adviewer/fastdds_udp.xml"
    assert f"{FASTDDS_UDP}:/adviewer/fastdds_udp.xml:ro" in plan.mounts and FASTDDS_UDP.is_file()
    own = _like()
    own["Config"]["Env"] = ["ROS_DISTRO=jazzy", "RMW_IMPLEMENTATION=rmw_fastrtps_cpp", "FASTRTPS_DEFAULT_PROFILES_FILE=/cfg/fast.xml"]
    assert plan_probe(_opts(), "adviewer-probe:dev", PROBE_ENV, like=own, install=INSTALL).env["FASTRTPS_DEFAULT_PROFILES_FILE"] == "/cfg/fast.xml"
    cyclone = plan_probe(_opts(), "adviewer-probe:dev", PROBE_ENV, like=_like(), install=INSTALL)
    assert "FASTRTPS_DEFAULT_PROFILES_FILE" not in cyclone.env
    assert "FASTRTPS_DEFAULT_PROFILES_FILE" not in plan_probe(_opts(), "adviewer-probe:dev", PROBE_ENV).env  # default: Cyclone
