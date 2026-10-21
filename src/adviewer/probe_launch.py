"""How `adviewer probe run` starts the probe so that it sees what Autoware sees (D32).

DDS discovery works only when the probe shares Autoware's network, DDS implementation, domain
and DDS configuration, and the probe can measure a topic only when that topic's message package
is installed where it runs. Setups differ (Autoware on the host network; in a Docker compose
network next to a simulator and a bridge; with a loopback-only CycloneDDS file; with shared
memory), so the probe runs one of three ways:

- probe image (default): ROS 2 + the probe, host network. Topics whose message types it lacks
  are listed but not measured.
- `--like CONTAINER`: the container's own image (all of its message packages), its network and
  IPC namespaces, its ROS/DDS environment and the DDS config files it mounts. The probe's
  binaries are mounted from the probe image, so both must be the same ROS 2 distribution
  (`adviewer probe build --base <image>` builds the probe for another one).
- `--in-image IMAGE`: another image (e.g. Autoware's) with the network and DDS options given.
- `--native`: Autoware built on this machine. The probe is built once in its own overlay
  workspace on top of ROS 2 and Autoware (`adviewer probe build --native`) and runs in this
  shell's environment, so it has every Autoware message type and the user's DDS settings.
`plan_probe` and `docker_run_argv` are pure (data in, command out); the functions at the end
ask Docker.
"""
from __future__ import annotations

import json
import re
import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PROBE_LABEL = "adviewer.probe"
PROBE_VERSION = 3  # 2: CycloneDDS, any ROS_DISTRO, unknown types skipped; 3: the probe binary is PID 1 (stops cleanly)
# `ros2 run` does not pass SIGINT/SIGTERM on to the program it starts, so scripts exec the binary itself
EXEC_PROBE = 'exec "$(ros2 pkg prefix adviewer_probe)/lib/adviewer_probe/probe"'
OUT_DIR = "/adviewer/out"
INSTALL_DIR = "/adviewer/probe"
CYCLONE_FILE = "/adviewer/cyclonedds.xml"
FASTDDS_FILE = "/adviewer/fastdds_udp.xml"
FASTDDS_UDP = Path(__file__).resolve().parent / "probe" / "fastdds_udp.xml"
SETUPS = ["/opt/autoware/setup.bash", "/autoware/install/setup.bash"]  # sourced when present
DDS_ENV = re.compile(r"^(ROS_DOMAIN_ID|ROS_LOCALHOST_ONLY|ROS_AUTOMATIC_DISCOVERY_RANGE|ROS_STATIC_PEERS|"
                     r"ROS_DISCOVERY_SERVER|ROS_SUPER_CLIENT|RMW_\w+|CYCLONEDDS\w*|FASTRTPS\w*|FASTDDS\w*|ZENOH\w*)$")
CONFIG_ENV = ("CYCLONEDDS_URI", "FASTRTPS_DEFAULT_PROFILES_FILE", "FASTDDS_DEFAULT_PROFILES_FILE",
              "ZENOH_ROUTER_CONFIG_URI", "ZENOH_SESSION_CONFIG_URI")


class ProbeSetupError(Exception):
    """A setup the probe cannot join as asked; the message says what to do."""


@dataclass
class ProbeOptions:
    out: Path  # dump file on this machine
    window: float = 5.0
    loop: bool = True
    rmw: str | None = None  # None: the container's (--like), else rmw_cyclonedds_cpp
    domain: int | None = None  # None: the container's (--like), else 0
    network: str | None = None  # None: the container's (--like), else host
    env: dict[str, str] = field(default_factory=dict)
    cyclone_config: Path | None = None
    setup: list[str] = field(default_factory=list)
    include: str = ""
    exclude: str = ""
    user: str | None = None  # "uid:gid"; None = root
    name: str = "adviewer-probe"
    selinux: bool = False  # SELinux enforcing on the host: the probe needs label=disable to read its mounts


def _env_list(items: list[str]) -> dict[str, str]:
    return dict(item.split("=", 1) for item in items if "=" in item)


def config_paths(env: dict[str, str]) -> list[str]:
    """Absolute paths of DDS config files named by the environment (CYCLONEDDS_URI may list several)."""
    paths = []
    for key in CONFIG_ENV:
        for part in env.get(key, "").split(","):
            part = part.strip()
            part = part.removeprefix("file://")
            if part.startswith("/"):
                paths.append(part)
    return paths


def _mount_for(path: str, mounts: list[dict[str, Any]]) -> list[str]:
    """The container's mount that holds `path`, read-only, for the probe."""
    hits = [m for m in mounts if path == m.get("Destination") or path.startswith(str(m.get("Destination", "")).rstrip("/") + "/")]
    if not hits:
        return []
    m = max(hits, key=lambda m: len(str(m.get("Destination", ""))))
    source = m.get("Name") if m.get("Type") == "volume" else m.get("Source")
    return ["-v", f"{source}:{m['Destination']}:ro"] if source else []


def _distro(env: dict[str, str]) -> str:
    return env.get("ROS_DISTRO", "")


@dataclass
class ProbePlan:
    """Where and how the probe runs; rendered as `docker run` argv."""
    image: str
    network: str  # host | container:NAME | a Docker network
    ipc: str | None
    env: dict[str, str]
    mounts: list[str]  # "source:target[:ro]"
    user: str | None
    ros_args: list[str]
    script: str | None  # bash script for another image; None = the probe image's own entrypoint
    name: str
    notes: list[str]
    like_name: str = ""
    selinux: bool = False


def _ros_args(opts: ProbeOptions, output: str) -> list[str]:
    params = [f"output:={output}", f"window:={opts.window}", f"loop:={str(opts.loop).lower()}"]
    params += [f"include:={opts.include}"] if opts.include else []
    params += [f"exclude:={opts.exclude}"] if opts.exclude else []
    return ["--ros-args"] + [x for p in params for x in ("-p", p)]


def plan_probe(opts: ProbeOptions, probe_image: str, probe_env: dict[str, str],
               like: dict[str, Any] | None = None, in_image: str | None = None,
               install: Path | None = None) -> ProbePlan:
    """How the probe joins the setup. `like` is `docker inspect` of a running container;
    `probe_env` the probe image's environment (its ROS_DISTRO)."""
    notes: list[str] = []
    env: dict[str, str] = {}
    network: str = "host"
    ipc: str | None = "host"
    mounts: list[str] = []
    image = probe_image
    name = ""
    if like is not None:
        name = str(like.get("Name", "")).lstrip("/")
        if not like.get("State", {}).get("Running", False):
            raise ProbeSetupError(f"container {name} is not running: start your stack first")
        cenv = _env_list(like.get("Config", {}).get("Env") or [])
        theirs, ours = _distro(cenv), _distro(probe_env)
        if theirs and ours and theirs != ours:
            raise ProbeSetupError(
                f"{name} runs ROS 2 {theirs}, the probe is built for {ours}: build it for {theirs} with "
                f"`adviewer probe build --base {like.get('Config', {}).get('Image', '<its image>')} --image adviewer-probe:{theirs}` "
                f"and run with --image adviewer-probe:{theirs}")
        image = str(like.get("Config", {}).get("Image") or like.get("Image"))
        env = {k: v for k, v in cenv.items() if DDS_ENV.match(k)}
        host = like.get("HostConfig", {})
        mode = str(host.get("NetworkMode") or "default")
        if mode == "none":
            raise ProbeSetupError(f"{name} has no network (network_mode: none); the probe cannot discover anything")
        network = "host" if mode == "host" else mode if mode.startswith("container:") else f"container:{name}"
        their_ipc = str(host.get("IpcMode") or "")
        ipc = ("host" if their_ipc == "host" else their_ipc if their_ipc.startswith("container:")
               else f"container:{name}" if their_ipc == "shareable" else None)
        for path in config_paths(env):
            m = _mount_for(path, like.get("Mounts") or [])
            if m and m[1] not in mounts:
                mounts.append(m[1])
            elif not m:
                notes.append(f"{path} (from the DDS environment) is inside {name}'s image; the probe uses that image too")
        notes.append(f"like {name}: image {image}, network {network}, {len(env)} ROS/DDS setting(s) copied")
    elif in_image:
        image = in_image
    if like is None:
        env["RMW_IMPLEMENTATION"] = opts.rmw or "rmw_cyclonedds_cpp"
        env["ROS_DOMAIN_ID"] = str(opts.domain if opts.domain is not None else 0)
        network = opts.network or "host"
    else:
        if opts.rmw:
            env["RMW_IMPLEMENTATION"] = opts.rmw
        if opts.domain is not None:
            env["ROS_DOMAIN_ID"] = str(opts.domain)
        if opts.network:
            network, ipc = opts.network, None
    if opts.cyclone_config is not None:
        mounts.append(f"{opts.cyclone_config.resolve()}:{CYCLONE_FILE}:ro")
        env["CYCLONEDDS_URI"] = f"file://{CYCLONE_FILE}"
    env.update(opts.env)
    # Fast DDS (also the ROS 2 default when RMW_IMPLEMENTATION is unset) sends data through shared
    # memory to what looks like the same machine; across containers or users that memory is not
    # shared, discovery still works and no data arrives. Unless the setup has its own Fast DDS profile,
    # the probe uses UDP only.
    rmw = env.get("RMW_IMPLEMENTATION") or ("rmw_fastrtps_cpp" if like is not None or in_image else "")
    if rmw.startswith("rmw_fastrtps") and not {"FASTRTPS_DEFAULT_PROFILES_FILE", "FASTDDS_DEFAULT_PROFILES_FILE"} & set(env):
        mounts.append(f"{FASTDDS_UDP}:{FASTDDS_FILE}:ro")
        env["FASTRTPS_DEFAULT_PROFILES_FILE"] = env["FASTDDS_DEFAULT_PROFILES_FILE"] = FASTDDS_FILE
        notes.append("Fast DDS: the probe receives over UDP (shared memory does not cross containers or users)")
    if opts.user:
        env.setdefault("HOME", "/tmp")
        env.setdefault("ROS_LOG_DIR", "/tmp/ros-log")
    mounts.insert(0, f"{opts.out.resolve().parent}:{OUT_DIR}")
    ros_args = _ros_args(opts, f"{OUT_DIR}/{opts.out.name}")
    script = None
    if like is not None or in_image:
        if install is None:
            raise ProbeSetupError("internal: the probe's install folder is needed to run inside another image")
        mounts.append(f"{install.resolve()}:{INSTALL_DIR}:ro")
        sources = ['. "/opt/ros/${ROS_DISTRO:-jazzy}/setup.bash"']
        sources += [f"[ -f {shlex.quote(s)} ] && . {shlex.quote(s)}" for s in [*SETUPS, *opts.setup]]
        run = f". {INSTALL_DIR}/setup.bash && {EXEC_PROBE} " + " ".join(shlex.quote(a) for a in ros_args)
        script = "; ".join([*sources, run])
    return ProbePlan(image=image, network=network, ipc=ipc, env=env, mounts=mounts, user=opts.user,
                     ros_args=ros_args, script=script, name=opts.name, notes=notes,
                     like_name=name, selinux=opts.selinux)


def docker_run_argv(plan: ProbePlan) -> list[str]:
    cmd = ["docker", "run", "--rm", "--name", plan.name, "--network", plan.network]
    cmd += ["--ipc", plan.ipc] if plan.ipc else []
    cmd += ["--user", plan.user] if plan.user else []
    cmd += ["--security-opt", "label=disable"] if plan.selinux else []
    for k, v in sorted(plan.env.items()):
        cmd += ["-e", f"{k}={v}"]
    for m in plan.mounts:
        cmd += ["-v", m]
    if plan.script is None:
        return cmd + [plan.image, *plan.ros_args]  # the probe image's own entrypoint
    return cmd + ["--entrypoint", "bash", plan.image, "-c", plan.script]


def build_command(opts: ProbeOptions, probe_image: str, probe_env: dict[str, str],
                  like: dict[str, Any] | None = None, in_image: str | None = None,
                  install: Path | None = None) -> tuple[list[str], list[str]]:
    """The `docker run` for the probe, and notes for the user."""
    plan = plan_probe(opts, probe_image, probe_env, like=like, in_image=in_image, install=install)
    return docker_run_argv(plan), plan.notes


# ---- native: Autoware built on this machine ----------------------------------------------------

def native_setups(given: list[str], env: dict[str, str], home: Path, exists: Any = Path.is_file) -> list[str]:
    """Setup files for a native run: the ones given, else ROS 2 (from ROS_DISTRO or /opt/ros/*)
    plus Autoware's install when found in the usual places."""
    if given:
        return given
    found: list[str] = []
    distro = env.get("ROS_DISTRO", "")
    ros = [Path(f"/opt/ros/{distro}/setup.bash")] if distro else sorted(Path("/opt/ros").glob("*/setup.bash"))
    found += [str(p) for p in ros[:1] if exists(p)]
    for candidate in (home / "autoware" / "install" / "setup.bash", Path("/opt/autoware/setup.bash")):
        if exists(candidate):
            found.append(str(candidate))
            break
    return found


def native_env(env: dict[str, str], venv: str) -> dict[str, str]:
    """This environment without adviewer's own virtualenv: ROS 2's build and launch tools must find
    the system Python with its ROS packages (catkin_pkg, rclpy), not the venv's python3."""
    if not venv:
        return dict(env)
    out = {k: v for k, v in env.items() if k not in ("VIRTUAL_ENV", "PYTHONHOME", "VIRTUAL_ENV_PROMPT")}
    for key in ("PATH", "PYTHONPATH"):
        if key in out:
            kept = [p for p in out[key].split(":") if p and not p.startswith(venv.rstrip("/") + "/") and p != venv]
            out[key] = ":".join(kept)
    return out


def native_build_script(package: Path, workspace: Path, setups: list[str]) -> str:
    """Bash that builds the probe package in its own overlay workspace on top of `setups`."""
    sources = " && ".join(f". {shlex.quote(s)}" for s in setups)
    src = workspace / "src" / "adviewer_probe"
    return (f"{sources} && rm -rf {shlex.quote(str(src))} && mkdir -p {shlex.quote(str(src.parent))} "
            f"&& cp -r {shlex.quote(str(package))} {shlex.quote(str(src))} && cd {shlex.quote(str(workspace))} "
            f"&& colcon build --packages-select adviewer_probe --cmake-args -DCMAKE_BUILD_TYPE=Release")


def native_run(opts: ProbeOptions, workspace: Path, setups: list[str]) -> tuple[list[str], dict[str, str]]:
    """The command and extra environment for the probe in this shell's ROS 2 / Autoware environment."""
    env: dict[str, str] = {}
    if opts.rmw:
        env["RMW_IMPLEMENTATION"] = opts.rmw
    if opts.domain is not None:
        env["ROS_DOMAIN_ID"] = str(opts.domain)
    if opts.cyclone_config is not None:
        env["CYCLONEDDS_URI"] = f"file://{opts.cyclone_config.resolve()}"
    env.update(opts.env)
    ros_args = _ros_args(opts, str(opts.out.resolve()))
    sources = [f". {shlex.quote(s)}" for s in [*setups, *opts.setup]]
    sources.append(f". {shlex.quote(str(workspace / 'install' / 'setup.bash'))}")
    script = " && ".join(sources) + f" && {EXEC_PROBE} " + " ".join(shlex.quote(a) for a in ros_args)
    return ["bash", "-c", script], env


# ---- Docker ----------------------------------------------------------------------------------

def _docker(args: list[str]) -> str:
    try:
        return subprocess.run(["docker", *args], capture_output=True, text=True, check=True, timeout=60).stdout
    except FileNotFoundError as exc:
        raise ProbeSetupError("docker not found: Online mode needs Docker (see `adviewer doctor --online`)") from exc
    except subprocess.CalledProcessError as exc:
        raise ProbeSetupError((exc.stderr or exc.stdout or str(exc)).strip().splitlines()[-1]) from exc


def inspect_container(name: str) -> dict[str, Any]:
    data = json.loads(_docker(["container", "inspect", name]))
    if not data:
        raise ProbeSetupError(f"no container named {name} (see `docker ps`)")
    return dict(data[0])


def container_instance(name: str) -> str:
    """Which run of the container is up: its id and start time, or "" when it is not running.
    A restart or a recreate gives it a new network namespace, and a probe that joined the old
    one sees nothing any more."""
    try:
        out = _docker(["container", "inspect", "--format", "{{.Id}} {{.State.Running}} {{.State.StartedAt}}", name]).split()
    except ProbeSetupError:
        return ""
    return f"{out[0]} {out[2]}" if len(out) == 3 and out[1] == "true" else ""


def remove_container(name: str) -> None:
    """Remove the probe's container if it is still there (its name is used again)."""
    try:
        _docker(["rm", "-f", name])
    except ProbeSetupError:
        pass


def selinux_enforcing(path: Path = Path("/sys/fs/selinux/enforce")) -> bool:
    try:
        return path.read_text(encoding="utf-8").strip() == "1"
    except OSError:
        return False


def container_distro(like: dict[str, Any]) -> str:
    return _distro(_env_list(like.get("Config", {}).get("Env") or []))


def probe_image_env(image: str) -> dict[str, str]:
    """The probe image's environment; fails with a clear message when it is missing or outdated."""
    try:
        data = json.loads(_docker(["image", "inspect", image]))[0]
    except ProbeSetupError as exc:
        raise ProbeSetupError(f"probe image {image} not found: run `adviewer probe build` ({exc})") from exc
    labels = (data.get("Config") or {}).get("Labels") or {}
    if int(labels.get(PROBE_LABEL, 0) or 0) < PROBE_VERSION:
        raise ProbeSetupError(f"probe image {image} is older than this adviewer: run `adviewer probe build` again")
    return _env_list((data.get("Config") or {}).get("Env") or [])


def probe_install(image: str, cache: Path) -> Path:
    """The probe's colcon install folder copied out of its image (once per image build)."""
    image_id = _docker(["image", "inspect", "--format", "{{.Id}}", image]).strip().split(":")[-1][:12]
    target = cache / f"probe-install-{image_id}"
    if (target / "setup.bash").is_file():
        return target
    cache.mkdir(parents=True, exist_ok=True)
    cid = _docker(["create", image]).strip()
    try:
        _docker(["cp", f"{cid}:/ws/install", str(target)])
    finally:
        _docker(["rm", cid])
    return target
