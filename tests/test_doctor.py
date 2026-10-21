"""Environment check (FR-12): both acceptance cases without touching the real machine."""
from pathlib import Path

from adviewer.doctor import check_docker, check_kernel, check_ros, run_doctor

PULL = "docker pull ghcr.io/autowarefoundation/autoware:universe-cuda-jazzy-1.9.0"


def runner(images: str, info: tuple[int, str] = (0, "29.0")):
    def run(cmd: list[str]) -> tuple[int, str]:
        return info if cmd[1] == "info" else (0, images)
    return run


def by_name(checks: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    return {c["name"]: c for c in checks}


def test_image_missing_says_so_and_gives_pull_command() -> None:
    c = by_name(check_docker(runner(""), lambda _: "/usr/bin/docker"))["autoware image"]
    assert c["status"] == "warn" and c["fix"] == PULL  # Offline mode works without it
    c = by_name(check_docker(runner(""), lambda _: "/usr/bin/docker", online=True))["autoware image"]
    assert c["status"] == "warn" and "running Autoware" in c["detail"]  # from any image, or built here


def test_autoware_from_an_image_of_your_own_is_not_an_error() -> None:
    def run(cmd: list[str]) -> tuple[int, str]:
        if cmd[1] == "info":
            return 0, "29.0"
        if cmd[1] == "ps":
            return 0, "c1\nc2\nc3\n"
        if cmd[1] == "inspect":
            return 0, ("/stack-autoware-1 PATH=/usr/bin ROS_DISTRO=jazzy RMW_IMPLEMENTATION=rmw_cyclonedds_cpp\n"
                       "/adviewer-probe-42 ROS_DISTRO=jazzy\n/postgres PATH=/usr/bin\n")
        return 0, ""  # no official image
    c = by_name(check_docker(run, lambda _: "/usr/bin/docker", online=True))["autoware image"]
    assert c["status"] == "info" and "stack-autoware-1" in c["detail"] and "probe-42" not in c["detail"]
    assert "--like" in c["detail"]


def test_pinned_image_present() -> None:
    images = "ghcr.io/autowarefoundation/autoware:universe-cuda-jazzy-1.9.0\n"
    c = by_name(check_docker(runner(images), lambda _: "/usr/bin/docker"))["autoware image"]
    assert c["status"] == "ok" and "1.9.0" in c["detail"]


def test_unknown_version_is_a_warning() -> None:
    images = "ghcr.io/autowarefoundation/autoware:universe-jazzy-1.7.1\n"
    c = by_name(check_docker(runner(images), lambda _: "/usr/bin/docker"))["autoware image"]
    assert c["status"] == "warn"


def test_no_docker_and_permission_denied() -> None:
    c = by_name(check_docker(runner(""), lambda _: None))["docker"]
    assert c["status"] == "warn" and "install" in c["fix"] and "--from-source" in c["fix"]  # Offline needs no Docker
    c = by_name(check_docker(runner(""), lambda _: None, online=True))["docker"]
    assert c["status"] == "error" and "--from-source" not in c["fix"]
    denied = runner("", (1, "permission denied while trying to connect to the Docker daemon"))
    c = by_name(check_docker(denied, lambda _: "/usr/bin/docker"))["docker"]
    assert "usermod -aG docker" in c["fix"]


def test_kernel_values(tmp_path: Path) -> None:
    proc = tmp_path / "proc"
    for rel, value in [("net/core/rmem_max", "2147483647"), ("net/ipv4/ipfrag_time", "30"),
                       ("net/ipv4/ipfrag_high_thresh", "134217728")]:
        (proc / rel).parent.mkdir(parents=True, exist_ok=True)
        (proc / rel).write_text(value + "\n")
    (tmp_path / "sys/class/net/lo").mkdir(parents=True)
    (tmp_path / "sys/class/net/lo/flags").write_text("0x1009\n")  # UP|LOOPBACK|MULTICAST
    checks = by_name(check_kernel(proc, tmp_path / "sys"))
    assert checks["net.core.rmem_max"]["status"] == "ok"
    assert checks["net.ipv4.ipfrag_time"]["fix"].startswith("sudo sysctl -w net.ipv4.ipfrag_time=3")
    assert checks["multicast on lo"]["status"] == "ok"
    assert check_kernel(tmp_path / "missing", tmp_path)[0]["status"] == "info"


def test_ros_and_dds(tmp_path: Path) -> None:
    xml = tmp_path / "cyclonedds.xml"
    xml.write_text('<SocketReceiveBufferSize min="10MB"/>')
    env = {"RMW_IMPLEMENTATION": "rmw_cyclonedds_cpp", "CYCLONEDDS_URI": f"file://{xml}",
           "ROS_LOCALHOST_ONLY": "1"}
    checks = by_name(check_ros(env, tmp_path / "noros"))
    assert checks["rmw"]["status"] == "ok" and checks["cyclonedds config"]["status"] == "ok"
    assert checks["ros localhost only"]["status"] == "warn"
    assert checks["ros 2"]["status"] == "info"  # Offline mode needs no ROS


def test_overall_status(tmp_path: Path) -> None:
    report = run_doctor(run=runner(""), which=lambda _: "/usr/bin/docker", env={},
                        proc_root=tmp_path, sys_root=tmp_path, ros_root=tmp_path / "noros")
    assert report["status"] == "warn" and "1.9.0" in report["releases"]
    checks = by_name(report["checks"])
    assert checks["rmw"]["status"] == "info"  # no ROS 2 on the host: belongs in the containers
    online = run_doctor(run=runner(""), which=lambda _: "/usr/bin/docker", env={}, online=True,
                        proc_root=tmp_path, sys_root=tmp_path, ros_root=tmp_path / "noros")
    assert online["status"] == "error"


def test_online_checks(tmp_path: Path) -> None:
    from adviewer.doctor import check_online

    def run(cmd: list[str]) -> tuple[int, str]:
        return (1, "No such image") if cmd[:3] == ["docker", "image", "inspect"] else (0, "")

    checks = by_name(check_online(run, "linux"))
    assert checks["probe image"]["status"] == "error" and checks["probe image"]["fix"] == "adviewer probe build"
    old = by_name(check_online(lambda cmd: (0, "2026-10-16T14:58:28 <no value>"), "linux"))["probe image"]
    assert old["status"] == "error" and "older adviewer" in old["detail"]  # built before D32: rebuild
    new = by_name(check_online(lambda cmd: (0, "2026-10-19T18:03:02 3"), "linux"))["probe image"]
    assert new["status"] == "ok" and "--like" in new["detail"]
    mac = by_name(check_online(run, "darwin"))["host networking"]
    assert mac["status"] == "warn" and "--like" in mac["fix"]  # Docker Desktop: join a container's network
    (tmp_path / "jazzy").mkdir()
    (tmp_path / "jazzy" / "setup.bash").write_text("", encoding="utf-8")
    native = by_name(check_online(run, "linux", which=lambda n: "/usr/bin/colcon", ros_root=tmp_path))
    assert native["native probe"]["status"] == "info" and "probe build --native" in native["native probe"]["detail"]
    assert "native probe" not in by_name(check_online(run, "linux", which=lambda n: None, ros_root=tmp_path))


def test_command_output_falls_back_to_stderr_when_stdout_is_blank(monkeypatch) -> None:
    import subprocess
    from types import SimpleNamespace

    from adviewer import doctor

    denied = "permission denied while trying to connect to the docker API at unix:///var/run/docker.sock"
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=1, stdout="\n", stderr=denied + "\n"))
    assert doctor.run_command(["docker", "info"]) == (1, denied)  # `docker info --format` prints a blank line
