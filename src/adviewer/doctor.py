"""Environment check (FR-12, Phase 3): what this machine needs, each problem with its fix.

Offline mode needs only Python (NFR-3); the Docker, ROS and DDS checks matter for running
Autoware and Online mode. Recommended DDS / kernel values are Autoware's documented ones
("DDS settings for ROS 2 and Autoware", docs.autoware.org, read 2026-10-16).
Never inspects images other than the official Autoware ones.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from adviewer.ingest.release_data import available_releases
from adviewer.probe_launch import PROBE_VERSION

OFFICIAL_REPO = "ghcr.io/autowarefoundation/autoware"
KERNEL = [  # (path under /proc/sys, recommended, "min" | "max", meaning)
    ("net/core/rmem_max", 2147483647, "min", "maximum socket receive buffer (2 GiB)"),
    ("net/ipv4/ipfrag_time", 3, "max", "IP fragment reassembly timeout (s)"),
    ("net/ipv4/ipfrag_high_thresh", 134217728, "min", "IP fragment memory (128 MiB)"),
]
IFF_MULTICAST = 0x1000

Runner = Callable[[list[str]], tuple[int, str]]


def run_command(cmd: list[str]) -> tuple[int, str]:
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 127, str(exc)
    return out.returncode, (out.stdout.strip() or out.stderr.strip())  # an empty line on stdout hides the error


def _check(name: str, status: str, detail: str, fix: str = "") -> dict[str, str]:
    return {"name": name, "status": status, "detail": detail, "fix": fix}


def check_docker(
    run: Runner, which: Callable[[str], str | None], online: bool = False
) -> list[dict[str, str]]:
    """Docker and the official Autoware image. Without the image Offline mode still works
    (it reads an extracted install space), so it is a warning unless `online`."""
    releases = available_releases()
    wanted = {r.release: r.load("release.yaml").get("image", "") for r in releases}
    pull = next(iter(wanted.values()), f"{OFFICIAL_REPO}:universe-cuda-jazzy-<release>")
    # Offline mode can read Autoware's source without Docker; Online mode and `extract` need it
    need = "error" if online else "warn"
    offline = "" if online else "  — or, for Offline mode without Docker: adviewer init --from-source"
    if which("docker") is None:
        return [
            _check("docker", need, "docker is not installed",
                   "install Docker Engine: https://docs.docker.com/engine/install/" + offline),
            _check("autoware image", need, "cannot check without docker", f"docker pull {pull}"),
        ]
    code, out = run(["docker", "info", "--format", "{{.ServerVersion}}"])
    if code != 0:
        fix = ("sudo usermod -aG docker $USER  (then log out and in)"
               if "permission denied" in out.lower() else "start the daemon: sudo systemctl start docker")
        return [_check("docker", need, f"docker daemon not reachable: {out[:160]}", fix + offline),
                _check("autoware image", need, "cannot check without docker", f"docker pull {pull}")]
    checks = [_check("docker", "ok", f"docker server {out}")]
    code, out = run(["docker", "images", "--format", "{{.Repository}}:{{.Tag}}", OFFICIAL_REPO])
    tags = [line.split(":", 1)[1] for line in out.splitlines() if line.startswith(OFFICIAL_REPO + ":")]
    if not tags:
        running = ros_containers(run) if online else []
        if running:  # Autoware from an image of your own: Online mode joins whichever container runs it
            checks.append(_check("autoware image", "info",
                                 f"no {OFFICIAL_REPO} image (needed only for `adviewer extract`); running ROS 2 "
                                 f"container(s): {', '.join(running[:6])} — run the probe with "
                                 f"`adviewer probe run --like <the one running Autoware>`"))
        else:
            checks.append(_check("autoware image", "warn",
                                 f"no {OFFICIAL_REPO} image found — needed for `adviewer extract`"
                                 + ("; Online mode needs a running Autoware (from any image, or built on "
                                    "this machine): start it, then check again" if online else
                                    "; Offline mode works without it (`adviewer init --from-source`)"),
                                 f"docker pull {pull}"))
        return checks
    versions = sorted({m.group(1) for t in tags if (m := re.search(r"(\d+\.\d+\.\d+)$", t))})
    known = [v for v in versions if v in wanted]
    detail = f"{len(tags)} image(s): " + ", ".join(sorted(tags))
    if known:
        checks.append(_check("autoware image", "ok", detail + f"; release data for {', '.join(known)}"))
    else:
        checks.append(_check(
            "autoware image", "warn", detail + "; no pinned release tag adviewer has data for",
            f"docker pull {pull}  (or add data/autoware-<release>/ for your version, NFR-2)"))
    return checks


def ros_containers(run: Runner) -> list[str]:
    """Running containers with ROS 2 inside (ROS_DISTRO set), without adviewer's own probes."""
    code, ids = run(["docker", "ps", "-q"])
    if code != 0 or not ids.split():
        return []
    code, out = run(["docker", "inspect", "--format", "{{.Name}} {{range .Config.Env}}{{.}} {{end}}", *ids.split()])
    if code != 0:
        return []
    names = [line.split()[0].lstrip("/") for line in out.splitlines() if " ROS_DISTRO=" in line]
    return sorted(n for n in names if not n.startswith("adviewer-probe"))


def check_ros(env: dict[str, str], ros_root: Path) -> list[dict[str, str]]:
    distro = env.get("ROS_DISTRO", "")
    installed = sorted(p.name for p in ros_root.glob("*")) if ros_root.is_dir() else []
    if distro:
        status, detail = ("ok" if distro == "jazzy" else "warn"), f"ROS_DISTRO={distro}"
    elif installed:
        status, detail = "info", f"installed under {ros_root}: {', '.join(installed)} (not sourced)"
    else:
        status, detail = "info", "no ROS 2 on the host — fine: Offline mode needs none; the probe runs in ros:jazzy"
    checks = [_check("ros 2", status, detail, "" if status != "warn" else "the pinned Autoware uses ROS 2 Jazzy")]
    host_ros = bool(distro or installed)
    # Without ROS 2 on the host, RMW and CycloneDDS settings belong inside the containers
    # (the Autoware image sets RMW itself; `adviewer probe run` passes it to the probe).
    soft = "warn" if host_ros else "info"
    rmw = env.get("RMW_IMPLEMENTATION", "")
    if rmw == "rmw_cyclonedds_cpp":
        checks.append(_check("rmw", "ok", "RMW_IMPLEMENTATION=rmw_cyclonedds_cpp"))
    else:
        checks.append(_check(
            "rmw", soft, f"RMW_IMPLEMENTATION={rmw or 'unset'} on this host; the Autoware image uses rmw_cyclonedds_cpp"
            + ("" if host_ros else " (no ROS 2 on the host, so this only matters inside containers)"),
            "export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp  (same in every container that talks to Autoware)"))
    uri = env.get("CYCLONEDDS_URI", "")
    if not uri:
        checks.append(_check("cyclonedds config", soft, "CYCLONEDDS_URI not set"
                             + ("" if host_ros else " on this host (only matters where ROS 2 runs)"),
                             "create cyclonedds.xml per docs.autoware.org (DDS settings) and "
                             "export CYCLONEDDS_URI=file:///absolute/path/cyclonedds.xml"))
    else:
        path = Path(uri.removeprefix("file://"))
        text = path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""
        m = re.search(r'SocketReceiveBufferSize\s+min="(\d+)\s*([kKmMgG]?)B?"', text)
        size = int(m.group(1)) * {"": 1, "k": 1e3, "m": 1e6, "g": 1e9}[m.group(2).lower()] if m else 0
        if not text:
            checks.append(_check("cyclonedds config", "error", f"{uri}: file not found", "fix the path"))
        elif size < 10e6:
            checks.append(_check("cyclonedds config", "warn", f"{uri}: SocketReceiveBufferSize min below 10MB",
                                 '<SocketReceiveBufferSize min="10MB"/> (docs.autoware.org)'))
        else:
            checks.append(_check("cyclonedds config", "ok", f"{uri}: receive buffer min {m.group(0) if m else ''}"))
    if env.get("ROS_LOCALHOST_ONLY") == "1":
        checks.append(_check("ros localhost only", "warn", "ROS_LOCALHOST_ONLY=1 breaks rmw_cyclonedds",
                             "unset ROS_LOCALHOST_ONLY; restrict to lo in cyclonedds.xml instead"))
    return checks


def check_kernel(proc_root: Path, sys_root: Path) -> list[dict[str, str]]:
    if not (proc_root / "net").is_dir():
        return [_check("kernel network", "info", "not Linux — kernel settings not checked")]
    checks = []
    for rel, want, how, meaning in KERNEL:
        f = proc_root / rel
        try:
            value = int(f.read_text().split()[0])
        except (OSError, ValueError, IndexError):
            checks.append(_check(rel, "info", f"{meaning}: not readable"))
            continue
        good = value >= want if how == "min" else value <= want
        key = rel.replace("/", ".")
        checks.append(_check(
            key, "ok" if good else "warn", f"{meaning}: {value} (recommended {'≥' if how == 'min' else '≤'} {want})",
            "" if good else f"sudo sysctl -w {key}={want}  (persist in /etc/sysctl.d/10-cyclone-max.conf); "
                            "without it large data such as point clouds or images can be lost"))
    flags = sys_root / "class/net/lo/flags"
    try:
        lo = int(flags.read_text().strip(), 16)
        on = bool(lo & IFF_MULTICAST)
        checks.append(_check("multicast on lo", "ok" if on else "warn",
                             "enabled" if on else "disabled",
                             "" if on else "sudo ip link set lo multicast on"))
    except (OSError, ValueError):
        pass
    return checks


def check_online(run: Runner, platform: str, which: Callable[[str], str | None] = shutil.which,
                 ros_root: Path = Path("/opt/ros")) -> list[dict[str, str]]:
    """What Online mode needs beyond the general checks: Linux host networking, probe image,
    and whether the probe can also be built natively (Autoware built on this machine)."""
    checks = []
    if platform != "linux":  # Docker Desktop: no host network, but a container's network can be joined
        checks.append(_check("host networking", "warn", f"{platform}: Docker Desktop cannot share the host network",
                             "run the probe with `adviewer probe run --like <autoware-container>`"))
    else:
        checks.append(_check("host networking", "ok", "Linux: the probe can join the host network"))
    code, out = run(["docker", "image", "inspect", "--format",
                     '{{.Created}} {{index .Config.Labels "adviewer.probe"}}', "adviewer-probe:dev"])
    version = out.split()[1] if code == 0 and len(out.split()) > 1 else ""
    if code != 0:
        checks.append(_check("probe image", "error", "adviewer-probe:dev not built", "adviewer probe build"))
    elif not version.isdigit() or int(version) < PROBE_VERSION:
        checks.append(_check("probe image", "error", f"adviewer-probe:dev built {out[:19]} by an older adviewer "
                             "(no CycloneDDS / Docker setup support)", "adviewer probe build"))
    else:
        checks.append(_check("probe image", "ok", f"adviewer-probe:dev built {out[:19]}; for Autoware in Docker use "
                             "`adviewer probe run --like <autoware-container>`"))
    if platform == "linux" and which("colcon") and any(ros_root.glob("*/setup.bash")):
        checks.append(_check("native probe", "info", "ROS 2 and colcon on this machine: for Autoware built here, "
                             "`adviewer probe build --native` then `adviewer probe run --native` (every Autoware message type)"))
    return checks


def run_doctor(
    run: Runner = run_command,
    which: Callable[[str], str | None] = shutil.which,
    env: dict[str, str] | None = None,
    proc_root: Path = Path("/proc/sys"),
    sys_root: Path = Path("/sys"),
    ros_root: Path = Path("/opt/ros"),
    online: bool = False,
    platform: str = sys.platform,
) -> dict[str, Any]:
    """All checks. Offline mode only needs Python; `online` adds what the live probe needs."""
    env = dict(os.environ) if env is None else env
    py_ok = sys.version_info >= (3, 10)
    checks = [_check("python", "ok" if py_ok else "error", sys.version.split()[0],
                     "" if py_ok else "Python ≥ 3.10 is required")]
    checks += check_docker(run, which, online)
    checks += check_ros(env, ros_root)
    checks += check_kernel(proc_root, sys_root)
    if online:
        checks += check_online(run, platform, which, ros_root)
    worst = "ok"
    for c in checks:
        if c["status"] == "error":
            worst = "error"
        elif c["status"] == "warn" and worst == "ok":
            worst = "warn"
    return {"status": worst, "checks": checks,
            "releases": [r.release for r in available_releases()]}
