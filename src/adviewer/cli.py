"""adviewer command line."""
from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path

import typer

from adviewer import __version__


def _safe_output() -> None:
    """Pipes, redirects and VS Code tasks on Windows often use cp1252: print the characters it
    lacks (→ ✓) as '?' instead of stopping with UnicodeEncodeError."""
    for stream in (sys.stdout, sys.stderr):
        encoding = (getattr(stream, "encoding", None) or "").lower().replace("-", "").replace("_", "")
        reconfigure = getattr(stream, "reconfigure", None)
        if encoding and not encoding.startswith("utf") and reconfigure:
            try:
                reconfigure(errors="replace")
            except (ValueError, OSError):
                pass


_safe_output()


def _port_free(host: str, port: int) -> bool:
    import socket

    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, port))
        except OSError:
            return False
    return True

app = typer.Typer(help="Autoware architecture explorer and mapping assistant.", no_args_is_help=True)
probe_app = typer.Typer(help="Online mode: build and run the C++ live probe (Docker, Linux).", no_args_is_help=True)
app.add_typer(probe_app, name="probe")
LOOPBACK = {"127.0.0.1", "localhost", "::1"}
PROBE_IMAGE = "adviewer-probe:dev"


def _version(value: bool) -> None:
    if value:
        typer.echo(f"adviewer {__version__}")
        raise typer.Exit()


@app.callback()
def _root(
    version: bool = typer.Option(False, "--version", callback=_version, is_eager=True, help="Show the version"),
) -> None:
    """Autoware Backpropagator: trace Autoware's topics, nodes, parameters and diagnostics."""


@app.command()
def ingest(
    autoware_root: Path = typer.Option(..., "--autoware-root", exists=True, file_okay=False),
    out: Path = typer.Option(Path("graph.json"), "--out"),
    entry_launch: Path | None = typer.Option(None, "--entry-launch", help="Top-level launch file to walk from"),
    source_root: Path | None = typer.Option(
        None, "--source-root", exists=True, file_okay=False,
        help="Source tree of the same release, for C++ scanning of an install space (D13, D22)",
    ),
    overlay: list[Path] = typer.Option(
        [], "--overlay", exists=True, file_okay=False,
        help="Further install space / workspace in overlay order (repeatable); its packages win",
    ),
) -> None:
    """Parse an Autoware install space and/or source tree into graph.json."""
    from adviewer.ingest import ingest_autoware_tree

    g = ingest_autoware_tree(
        autoware_root, str(entry_launch) if entry_launch else None, source_root, overlay
    )
    g.save(out)
    typer.echo(
        f"wrote {out}: {len(g.nodes)} nodes, {len(g.topics)} topics, {len(g.edges)} edges, "
        f"{len(g.msg_types)} msg types, {len(g.diag_units)} diag units, {len(g.meta.warnings)} warnings"
    )


@app.command("layer-table")
def layer_table(
    source_root: Path = typer.Option(..., "--source-root", exists=True, file_okay=False),
    release: str = typer.Option(..., "--release", help="e.g. 1.9.0"),
) -> None:
    """Regenerate data/autoware-<release>/layers.yaml from a source tree (F8, NFR-2)."""
    from adviewer.ingest.package_xml import index_packages
    from adviewer.ingest.release_data import DATA_DIR, write_layer_table

    out = DATA_DIR / f"autoware-{release}" / "layers.yaml"
    n = write_layer_table(index_packages(source_root), out, keep_rules_from=out)
    typer.echo(f"wrote {out}: {n} packages from source, existing name rules kept")


def _serve(
    graph: Path, host: str, port: int, live: Path | None, target: Path | None, token: str | None,
    no_auth: bool, allowed_host: list[str], ssl_certfile: Path | None, ssl_keyfile: Path | None,
    record: bool = True, auto_port: bool = True,
) -> None:
    """Start the web app. Loopback (the default) needs nothing else; any other address gets
    a token (generated unless given) and a warning when traffic is not encrypted."""
    import secrets

    import uvicorn

    from adviewer.api.app import create_app
    from adviewer.model import Graph

    exposed = host not in LOOPBACK
    if exposed and not no_auth and not token:
        token = secrets.token_urlsafe(24)
    # Host header check (blocks DNS rebinding): always on loopback; on a network address only
    # when --allowed-host names the hosts (the Docker setup does), else the token protects it.
    allowed = (LOOPBACK | set(allowed_host)) if (not exposed or allowed_host) else None
    scheme = "https" if ssl_certfile else "http"
    if not _port_free(host, port):  # before printing a URL that would lead to another server
        free = next((p for p in range(port + 1, port + 50) if _port_free(host, p)), None)
        if auto_port and free:
            typer.echo(f"port {port} is busy (another adviewer?) → using {free}")
            port = free
        else:
            typer.echo(f"port {port} is already in use" + (f"; try --port {free}" if free else ""), err=True)
            raise typer.Exit(1)
    shown = host if not exposed else "<this-machine-address>"
    typer.echo(f"adviewer: {scheme}://{shown}:{port}/" + (f"?token={token}" if token else ""))
    if exposed and no_auth:
        typer.echo("warning: listening on the network WITHOUT a token (--no-auth)", err=True)
    if exposed and not ssl_certfile:
        typer.echo("note: traffic is not encrypted — prefer an SSH tunnel "
                   f"(ssh -L {port}:127.0.0.1:{port} <host>) or --ssl-certfile/--ssl-keyfile", err=True)
    import signal

    def interrupted(*_: object) -> None:
        raise KeyboardInterrupt

    # a stopped VS Code task or `kill` (SIGTERM) ends like Ctrl+C, also while loading: uvicorn hands
    # the signal back to this handler after its shutdown, so the recorder below can let go of its lock
    signal.signal(signal.SIGTERM, interrupted)
    thread = stop = None
    if live is not None and record:  # the Live dashboard's history, unless another recorder keeps it
        from adviewer import recorder

        history = recorder.history_path_for(live)
        thread, stop = recorder.start(live, history)
        typer.echo(f"recording the live history into {history} (another recorder, e.g. `probe run`, takes precedence)")
    try:
        uvicorn.run(create_app(Graph.load(graph), live, target, token=token, allowed_hosts=allowed),
                    host=host, port=port, ssl_certfile=str(ssl_certfile) if ssl_certfile else None,
                    ssl_keyfile=str(ssl_keyfile) if ssl_keyfile else None, log_level="warning")
    except KeyboardInterrupt:
        pass
    finally:
        if thread and stop:
            stop.set()
            thread.join(timeout=3)  # releases the recorder's lock if it took over from `probe run`


@app.command()
def serve(
    graph: Path = typer.Argument(Path("graph.json"), exists=True),
    host: str = typer.Option("127.0.0.1", help="127.0.0.1 = this machine only (recommended)"),
    port: int | None = typer.Option(None, help="default 8765, or the next free port when that one is busy"),
    live: Path | None = typer.Option(
        None, "--live", help="probe_dump.json to watch for Online mode (re-read when it changes)"
    ),
    target: Path | None = typer.Option(
        None, "--target", exists=True, help="target.yaml for the mapping panel (Phase 5)"
    ),
    token: str | None = typer.Option(None, "--token", help="require this token (generated when --host is not loopback)"),
    no_auth: bool = typer.Option(False, "--no-auth", help="no token even on the network (not recommended)"),
    allowed_host: list[str] = typer.Option([], "--allowed-host", help="extra Host header to accept on loopback"),
    ssl_certfile: Path | None = typer.Option(None, "--ssl-certfile", exists=True),
    ssl_keyfile: Path | None = typer.Option(None, "--ssl-keyfile", exists=True),
    record_history: bool = typer.Option(True, "--record/--no-record", help="with --live: keep every probe window for the Live dashboard"),
) -> None:
    """Serve the web UI for a graph.json (open in a browser or VS Code's Simple Browser)."""
    _serve(graph, host, port or 8765, live, target, token, no_auth, allowed_host, ssl_certfile, ssl_keyfile,
           record_history, auto_port=port is None)


NO_DOCKER = ("No Docker, or no room for the image? Read Autoware from its source instead (the same graph):\n"
             "  adviewer init --from-source --force && adviewer fetch-source && adviewer up")


@app.command()
def init(
    install_space: str = typer.Option("autoware/install", help="Autoware install space (relative to here)"),
    source: str = typer.Option("autoware/src", help="source of the same release (optional)"),
    entry_launch: str = typer.Option(
        "autoware_launch/share/autoware_launch/launch/autoware.launch.xml",
        help="top-level launch file, relative to the install space (found by package in a source tree)"),
    from_source: bool = typer.Option(False, "--from-source", help="read Autoware from its source "
                                     "(`adviewer fetch-source`): no Docker image needed"),
    force: bool = typer.Option(False, "--force", help="replace an existing adviewer.yaml"),
    print_tasks: bool = typer.Option(False, "--print-tasks", help="only print the VS Code tasks"),
) -> None:
    """Set adviewer up in this project folder: adviewer.yaml + VS Code tasks."""
    import json

    from adviewer import project

    if print_tasks:
        typer.echo(json.dumps(project.VSCODE_TASKS, indent=2))
        return
    if from_source:
        install_space = source
    for line in project.init(Path.cwd(), install_space, source, entry_launch, force):
        typer.echo(f"  {line}")
    if from_source:
        typer.echo("next: adviewer fetch-source, then adviewer up")
    else:
        typer.echo("next: adviewer extract (copies Autoware's files out of its Docker image), adviewer fetch-source, "
                   "adviewer up\n      no Docker? adviewer init --from-source --force")


@app.command()
def extract(
    image: str | None = typer.Option(None, help="Autoware image: the release's official one, or your own"),
    out: Path | None = typer.Option(None, help="target folder (default: install_space from adviewer.yaml)"),
    release: str | None = typer.Option(None, help="Autoware release, e.g. 1.9.0"),
    prefix: str | None = typer.Option(None, help="install space inside the image (default: found by looking)"),
    yes: bool = typer.Option(False, "--yes", help="download the image even when the disk looks too small"),
) -> None:
    """Copy the install space's launch/config/msg files out of an Autoware Docker image (about
    30-100 MB). The first time, Docker downloads the image. No Docker: `adviewer init --from-source`."""
    import shutil
    import subprocess

    from adviewer import doctor, project

    rel = project.release(release)
    meta = rel.load("release.yaml") or {}
    if out is None:
        cfg = project.load()
        out = cfg.path(cfg.install_space)
    img = image or meta["image"]
    docker = doctor.check_docker(doctor.run_command, shutil.which, online=True)[0]
    if docker["status"] == "error":
        typer.echo(f"adviewer extract needs Docker: {docker['detail']}\n  fix: {docker['fix']}\n{NO_DOCKER}", err=True)
        raise typer.Exit(2)
    if subprocess.run(["docker", "image", "inspect", img], check=False, capture_output=True).returncode != 0:
        free = shutil.disk_usage(_docker_root()).free / 1e9
        size = float(meta.get("image_download_gb") or 0) if img == meta.get("image") else 0.0
        if size:
            need = 4 * size  # measured: a pulled image takes about 4x its download (Docker's containerd store)
            typer.echo(f"{img} is not on this machine: Docker downloads it first ({size:.1f} GB download, "
                       f"about {need:.0f} GB on disk; {free:.0f} GB free)")
            if free < need and not yes:
                typer.echo("probably not enough disk space for it. Free some space and run this again, run "
                           f"`adviewer extract --yes` to try anyway, or skip the image.\n{NO_DOCKER}", err=True)
                raise typer.Exit(1)
        else:
            typer.echo(f"{img} is not on this machine: Docker downloads it first ({free:.0f} GB free)")
        if project.run(["docker", "pull", img]):
            typer.echo(f"could not download {img}: see Docker's message above (image name, network, "
                       f"login or disk space).\n{NO_DOCKER}", err=True)
            raise typer.Exit(1)
    out.mkdir(parents=True, exist_ok=True)
    where = prefix
    if where is None:  # images differ: find the install space instead of assuming /opt/autoware
        found = subprocess.run(project.detect_prefix_command(img, meta.get("image_prefix", "/opt/autoware")),
                               check=False, capture_output=True, text=True)
        if found.returncode != 0:
            said = (found.stderr or found.stdout).strip().splitlines()[-1:] or [f"exit {found.returncode}"]
            typer.echo(f"could not look inside {img}: {said[0]}", err=True)
            raise typer.Exit(found.returncode)
        hits = [line.strip() for line in found.stdout.splitlines() if line.strip()]
        if not hits:
            typer.echo(f"no install space found in {img} (looked in {', '.join(project.PREFIXES)}): "
                       "pass --prefix <the folder in the image that holds Autoware's packages>", err=True)
            raise typer.Exit(1)
        where = hits[0]
        if where != meta.get("image_prefix"):
            typer.echo(f"install space in this image: {where}")
    code = project.run(project.extract_command(img, out, where))
    if code:
        typer.echo(f"extract failed (exit {code}): see Docker's message above", err=True)
        raise typer.Exit(code)
    files = sum(1 for f in out.rglob("*") if f.is_file() and f.name != "COLCON_IGNORE")
    if not files:
        typer.echo(f"nothing copied from {where} in {img}: is it the install space? pass --prefix <folder>", err=True)
        raise typer.Exit(1)
    typer.echo(f"install space ready in {out} ({files} files)")


@app.command("fetch-source")
def fetch_source(
    out: Path | None = typer.Option(None, help="target folder (default: source from adviewer.yaml)"),
    release: str | None = typer.Option(None, help="Autoware release, e.g. 1.9.0"),
) -> None:
    """Shallow-clone the source companion of a release (C++, READMEs) at its exact tags."""
    from adviewer import project

    if out is None:
        cfg = project.load()
        out = cfg.path(cfg.source or "autoware/src")
    notes = 0
    for cmd in project.fetch_source_commands(project.release(release), out):
        folder, version = Path(cmd[-1]), cmd[cmd.index("--branch") + 1]
        if folder.exists():
            problem = project.clone_problem(folder, version)
            typer.echo(f"  {problem}" if problem else f"  exists, kept: {folder} ({version})", err=bool(problem))
            notes += bool(problem)
            continue
        if project.run(cmd):
            typer.echo("git could not clone it: see its message above (network, proxy or disk space). "
                       "The clones made so far are kept; run this again to continue.", err=True)
            raise typer.Exit(1)
    typer.echo(f"source ready in {out}" + (f" ({notes} note(s) above)" if notes else ""))


def _graph_outdated(graph: Path, inputs: list[Path]) -> str:
    """Why a saved graph should be read again ("" when it is current): it is empty (e.g. saved
    after an `extract` that stopped early), or adviewer.yaml / the Autoware folders changed since."""
    import json

    try:
        if not json.loads(graph.read_text(encoding="utf-8")).get("nodes"):
            return "the saved graph is empty"
        made = graph.stat().st_mtime
    except (OSError, ValueError):
        return "the saved graph cannot be read"
    newer = [p for p in inputs if p.exists() and p.stat().st_mtime > made]
    return f"{newer[0].name} changed since the graph was made" if newer else ""


@app.command()
def up(
    reingest: bool = typer.Option(False, "--reingest", help="read Autoware again even if the graph looks current"),
    ingest_only: bool = typer.Option(False, "--ingest-only", help="ingest, then stop"),
    online: bool = typer.Option(False, "--online", help="watch the probe dump for live health"),
) -> None:
    """Ingest (when needed) and serve, using adviewer.yaml — the everyday command."""
    from adviewer import project
    from adviewer.ingest import ingest_autoware_tree

    cfg = project.load()
    graph = cfg.path(cfg.graph)
    install = cfg.path(cfg.install_space)
    source = cfg.path(cfg.source) if cfg.source and cfg.path(cfg.source).is_dir() else None
    why = "" if reingest or ingest_only or not graph.exists() else _graph_outdated(
        graph, [cfg.root / project.CONFIG, install, *([source] if source else [])])
    if why:
        typer.echo(f"adviewer: {why}: reading Autoware again")
    if reingest or ingest_only or not graph.exists() or why:
        if not install.is_dir():
            typer.echo(f"install space not found: {install} — run `adviewer extract`, or fix adviewer.yaml\n{NO_DOCKER}",
                       err=True)
            raise typer.Exit(1)
        g = ingest_autoware_tree(install, str(install / cfg.entry_launch), source, [cfg.path(o) for o in cfg.overlays])
        if not g.nodes:  # not saved: the next `up` would serve the empty graph
            typer.echo(f"no Autoware nodes found in {install}: the folder is empty or not Autoware (did "
                       f"`adviewer extract` or `adviewer fetch-source` stop early?)\n{NO_DOCKER}", err=True)
            raise typer.Exit(1)
        graph.parent.mkdir(parents=True, exist_ok=True)
        g.save(graph)
        typer.echo(f"adviewer: read {len(g.nodes)} nodes, {len(g.topics)} topics, "
                   f"{len(g.meta.warnings)} warnings → {graph}")
    if ingest_only:
        return
    live = cfg.path(cfg.live_dump)
    target = cfg.path(cfg.target) if cfg.target else None
    _serve(graph, cfg.host, cfg.port, live if (online or live.exists()) else None, target,
           None, False, [], None, None)


def _docker_root() -> str:
    import subprocess

    try:
        root = subprocess.run(["docker", "info", "--format", "{{.DockerRootDir}}"], check=False,
                              capture_output=True, text=True, timeout=20).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        root = ""
    return root if root and Path(root).exists() else str(Path.home())


def _probe_env(image: str, base: str = "ros:jazzy") -> dict[str, str]:
    """The probe image's environment; builds the image first when it is missing or outdated."""
    from adviewer import probe_launch, project

    try:
        return probe_launch.probe_image_env(image)
    except probe_launch.ProbeSetupError as exc:
        typer.echo(f"{exc} — building it now (one-off, 1–3 min)…")
    context = Path(__file__).resolve().parent / "probe"
    if project.run(["docker", "build", "--build-arg", f"BASE={base}", "-t", image, str(context)]) != 0:
        raise probe_launch.ProbeSetupError(f"could not build {image} on {base}; see the output above")
    return probe_launch.probe_image_env(image)


def _like_target(like: str, image: str) -> tuple[dict[str, object], str, dict[str, str]]:
    """The --like container, and the probe image matching its ROS 2 distribution (built when needed)."""
    from adviewer import probe_launch

    target = probe_launch.inspect_container(like)
    probe_env = _probe_env(image)
    theirs, ours = probe_launch.container_distro(target), probe_env.get("ROS_DISTRO", "")
    if theirs and ours and theirs != ours:
        typer.echo(f"{like} runs ROS 2 {theirs}, {image} is built for {ours}: using adviewer-probe:{theirs}")
        image = f"adviewer-probe:{theirs}"
        probe_env = _probe_env(image, base=f"ros:{theirs}")
    return target, image, probe_env


def _native_workspace(workspace: Path | None) -> Path:
    import os

    cache = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    return (workspace or cache / "adviewer" / "probe-native").expanduser().resolve()


@probe_app.command("build")
def probe_build(
    image: str = typer.Option(PROBE_IMAGE, help="image name to build"),
    base: str = typer.Option("ros:jazzy", help="ROS 2 image to build on, e.g. your Autoware -devel image for another distribution"),
    native: bool = typer.Option(False, "--native", help="Autoware built on this machine: build the probe here, on top of it (no Docker)"),
    setup: list[str] = typer.Option([], "--setup", help="with --native: setup.bash to build on, in order (default: ROS 2, then Autoware if found)"),
    workspace: Path | None = typer.Option(None, help="with --native: the probe's own overlay workspace"),
) -> None:
    """Build the C++ probe: a Docker image (default), or natively on top of your ROS 2 / Autoware."""
    import json
    import os
    import sys

    from adviewer import probe_launch, project

    context = Path(__file__).resolve().parent / "probe"
    if not native:
        raise typer.Exit(project.run(["docker", "build", "--build-arg", f"BASE={base}", "-t", image, str(context)]))
    if sys.platform == "win32":
        typer.echo("--native needs ROS 2 on Linux; on this machine use the Docker probe", err=True)
        raise typer.Exit(2)
    setups = probe_launch.native_setups(setup, dict(os.environ), Path.home())
    if not setups:
        typer.echo("no ROS 2 found here (/opt/ros/*/setup.bash): pass --setup <your ROS 2 setup.bash>, "
                   "or use the Docker probe (`adviewer probe build`)", err=True)
        raise typer.Exit(2)
    ws = _native_workspace(workspace)
    ws.mkdir(parents=True, exist_ok=True)
    typer.echo("building on: " + ", ".join(setups))
    import subprocess

    venv = sys.prefix if sys.prefix != sys.base_prefix else ""  # adviewer's own virtualenv, if any
    code = subprocess.call(["bash", "-c", probe_launch.native_build_script(context / "cpp", ws, setups)],
                           env=probe_launch.native_env(dict(os.environ), venv))
    if code == 0:
        (ws / "adviewer-native.json").write_text(json.dumps({"setups": setups}, indent=1), encoding="utf-8")
        typer.echo(f"probe built in {ws}; run it with `adviewer probe run --native`")
    raise typer.Exit(code)


@probe_app.command("run")
def probe_run(
    out: Path | None = typer.Option(None, help="dump file (default: live.dump from adviewer.yaml)"),
    like: str | None = typer.Option(None, "--like", help="run like this container: its image, network, IPC and ROS/DDS "
                                    "settings (Autoware in Docker, compose stacks, custom DDS configs)"),
    in_image: str | None = typer.Option(None, "--in-image", help="run inside this image (e.g. Autoware's, for all its message types)"),
    native: bool = typer.Option(False, "--native", help="Autoware built on this machine: run the probe here, in your ROS 2 / Autoware "
                                "environment (after `adviewer probe build --native`)"),
    workspace: Path | None = typer.Option(None, help="with --native: the probe's overlay workspace"),
    network: str | None = typer.Option(None, help="host (default), a Docker network, or container:NAME"),
    rmw: str | None = typer.Option(None, help="RMW (default rmw_cyclonedds_cpp; with --like: the container's)"),
    domain: int | None = typer.Option(None, help="ROS_DOMAIN_ID (default 0; with --like: the container's)"),
    cyclone_config: Path | None = typer.Option(None, "--cyclone-config", exists=True, dir_okay=False,
                                               help="CycloneDDS XML to use, e.g. your stack's when it limits interfaces or peers"),
    env: list[str] = typer.Option([], "--env", help="extra KEY=VALUE for the probe (repeatable)"),
    setup: list[str] = typer.Option([], "--setup", help="extra setup.bash to source with --like / --in-image (repeatable)"),
    include: str = typer.Option("", help="regex: measure only matching topics (e.g. '^/sensing/')"),
    exclude: str = typer.Option("", help="regex: skip matching topics (replaces the default /rosout, /parameter_events)"),
    window: float = typer.Option(5.0, help="seconds per measurement window"),
    loop: bool = typer.Option(True, help="keep measuring and rewriting the dump"),
    root: bool = typer.Option(False, "--root", help="run as root (default: your user, so the files are yours)"),
    image: str = typer.Option(PROBE_IMAGE, help="the probe image (from `adviewer probe build`)"),
    record_history: bool = typer.Option(True, "--record/--no-record",
                                        help="also keep every window for the Live dashboard (history file next to the dump)"),
) -> None:
    """Run the probe next to a running Autoware (Linux). See `--like` for Docker setups."""
    import os
    import sys

    from adviewer import probe_launch, project

    if out is None:
        cfg = project.load()
        out = cfg.path(cfg.live_dump)
    out.parent.mkdir(parents=True, exist_ok=True)
    bad = [e for e in env if "=" not in e]
    if bad:
        typer.echo(f"--env needs KEY=VALUE, got: {', '.join(bad)}", err=True)
        raise typer.Exit(2)
    user = None
    if sys.platform != "win32" and not root:  # a statement mypy understands; Windows has no uid/gid
        user = f"{os.getuid()}:{os.getgid()}"
    opts = probe_launch.ProbeOptions(
        out=out, window=window, loop=loop, rmw=rmw, domain=domain, network=network,
        env=dict(e.split("=", 1) for e in env), cyclone_config=cyclone_config, setup=setup,
        include=include, exclude=exclude, name=f"adviewer-probe-{os.getpid()}", user=user)
    import threading

    threading.Thread(target=_hint_if_empty, args=(out, like, native), daemon=True).start()
    if native:
        import json

        if like or in_image:
            typer.echo("--native runs the probe on this machine; it cannot be combined with --like or --in-image", err=True)
            raise typer.Exit(2)
        ws = _native_workspace(workspace)
        info = ws / "adviewer-native.json"
        if not info.is_file():
            typer.echo(f"no native probe in {ws}: run `adviewer probe build --native` first", err=True)
            raise typer.Exit(2)
        setups = json.loads(info.read_text(encoding="utf-8")).get("setups", [])
        cmd, extra = probe_launch.native_run(opts, ws, setups)
        env_note = ", ".join(f"{k}={v}" for k, v in sorted(extra.items())) or "your shell's ROS/DDS settings"
        typer.echo(f"native probe from {ws}; DDS: {env_note}")
        venv = sys.prefix if sys.prefix != sys.base_prefix else ""
        _run_with_history(out, cmd, record_history and loop, {**probe_launch.native_env(dict(os.environ), venv), **extra})
        return
    if not like and sys.platform != "linux" and (network or "host") == "host":
        typer.echo("Docker Desktop (macOS/Windows) has no host network, so this probe would see nothing: "
                   "run it with --like <autoware-container>", err=True)
        raise typer.Exit(2)
    opts.selinux = probe_launch.selinux_enforcing()
    try:
        if like:
            target, image, probe_env = _like_target(like, image)
        else:
            target, probe_env = None, _probe_env(image)
        install = probe_launch.probe_install(image, out.parent) if (like or in_image) else None
        cmd, notes = probe_launch.build_command(opts, image, probe_env, like=target, in_image=in_image, install=install)
    except probe_launch.ProbeSetupError as exc:
        typer.echo(f"adviewer probe run: {exc}", err=True)
        raise typer.Exit(2) from exc
    for note in notes:
        typer.echo(note)
    follow = None
    if like:
        def plan() -> list[str]:
            target, img, penv = _like_target(like, image)
            return probe_launch.build_command(opts, img, penv, like=target,
                                              install=probe_launch.probe_install(img, out.parent))[0]
        follow = _Follow(like, plan, opts.name)
    _run_with_history(out, cmd, record_history and loop, follow=follow, container=opts.name)


def _empty_hint(dump: dict[str, object], like: str | None, native: bool, containers: list[str]) -> str:
    """What to try when the probe's first window has no topics ("" when it has some)."""
    if dump.get("topics"):
        return ""
    if like:
        return (f"the probe sees no topics in {like}: is Autoware running there? (in a development "
                "container it is often started by hand, with `docker exec`)")
    if native:
        return "the probe sees no topics: is Autoware running, with this shell's ROS_DOMAIN_ID and RMW_IMPLEMENTATION?"
    hint = ("the probe sees no topics. Autoware in Docker, or with its own DDS settings? Run the probe like "
            "its container: `adviewer probe run --like <container>`")
    return hint + (f" (running ROS 2 containers: {', '.join(containers[:6])})" if containers else "")


def _hint_if_empty(out: Path, like: str | None, native: bool) -> None:
    """Background: once the first window of this run is written, say what to try if it is empty."""
    import json
    import time

    from adviewer import doctor

    start = time.time() - 1
    for _ in range(240):  # up to 2 minutes
        try:
            if out.stat().st_mtime >= start:
                dump = json.loads(out.read_text(encoding="utf-8"))
                break
        except (OSError, ValueError):
            pass
        time.sleep(0.5)
    else:
        return
    containers = doctor.ros_containers(doctor.run_command) if not (like or native) else []
    hint = _empty_hint(dump if isinstance(dump, dict) else {}, like, native, containers)
    if hint:
        typer.echo(hint, err=True)


class _Follow:
    """--like: the container the probe joined. When it is restarted or recreated it gets a new
    network namespace and the probe, still in the old one, would measure nothing: the probe is
    stopped and joins the container again once it runs."""

    def __init__(self, name: str, plan: Callable[[], list[str]], probe_name: str, poll: float = 2.0):
        from adviewer import probe_launch

        self.name, self.plan, self.probe_name, self.poll = name, plan, probe_name, poll
        self.seen = probe_launch.container_instance(name)

    def changed(self) -> bool:
        from adviewer import probe_launch

        return probe_launch.container_instance(self.name) != self.seen

    def rejoin(self) -> list[str]:
        """Wait until the container runs again; the probe's new command."""
        import time

        from adviewer import probe_launch

        probe_launch.remove_container(self.probe_name)
        told = False
        while True:
            now = probe_launch.container_instance(self.name)
            if now:
                try:
                    cmd = self.plan()
                except probe_launch.ProbeSetupError:
                    cmd = []
                if cmd:
                    self.seen = now
                    typer.echo(f"{self.name} was restarted: the probe joins it again")
                    return cmd
            if not told:
                typer.echo(f"{self.name} stopped: waiting for it to run again (Ctrl+C stops)")
                told = True
            time.sleep(self.poll)


def _run_with_history(out: Path, cmd: list[str], record: bool, env: dict[str, str] | None = None,
                      follow: _Follow | None = None, container: str | None = None) -> None:
    """Run the probe; meanwhile keep its windows for the Live dashboard. With `follow` (--like),
    the probe joins its container again after the container restarts. `container`: the probe's
    container in the Docker modes, removed at the end if it is still there."""
    import os
    import shlex
    import signal
    import subprocess
    import sys

    from adviewer import probe_launch, recorder

    stop = thread = None
    if record:
        history = recorder.history_path_for(out)
        typer.echo(f"recording every window into {history}")
        thread, stop = recorder.start(out, history)

    def start(cmd: list[str]) -> subprocess.Popen[bytes]:
        print("$", " ".join(shlex.quote(c) for c in cmd), flush=True)
        # its own process group: `ros2 run` does not pass Ctrl+C on to the probe, so stop the whole group
        return subprocess.Popen(cmd, env=env, start_new_session=os.name == "posix")

    def halt(proc: subprocess.Popen[bytes], hard: bool = False) -> None:
        """Stop the probe: Ctrl+C first; its container removed when that does not work in time
        (a probe whose network namespace is gone can hang while it shuts down)."""
        if proc.poll() is not None:
            return
        try:
            if hard and container:
                probe_launch.remove_container(container)
            elif sys.platform != "win32":  # a check mypy understands; Windows has no process groups
                os.killpg(proc.pid, signal.SIGINT)
            else:  # nor SIGINT for another process: stop the client, its container is removed below
                proc.terminate()
        except OSError:
            pass
        try:
            proc.wait(timeout=10)
            return
        except subprocess.TimeoutExpired:
            pass
        if container:
            probe_launch.remove_container(container)
        proc.kill()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass

    def interrupted(*_: object) -> None:
        raise KeyboardInterrupt

    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, interrupted)  # a stopped VS Code task or `kill` acts like Ctrl+C
    proc = start(cmd)
    try:
        while True:
            try:
                code = proc.wait(timeout=follow.poll if follow else None)
            except subprocess.TimeoutExpired:
                code = None
            if follow is None or not follow.changed():
                if code is None:
                    continue
                break
            halt(proc, hard=True)  # blind in the old network namespace: nothing to keep
            proc = start(follow.rejoin())
    except KeyboardInterrupt:  # stop the probe too, not only adviewer
        halt(proc)
        code = 0
    finally:
        if container:
            probe_launch.remove_container(container)
        if stop and thread:
            stop.set()
            thread.join(timeout=3)  # lets the recorder release its lock before the process ends
    raise typer.Exit(code)


@app.command()
def record(
    dump: Path | None = typer.Option(None, help="probe dump to watch (default: live.dump from adviewer.yaml)"),
    history: Path | None = typer.Option(None, help="history file (default: <dump>.history.jsonl)"),
) -> None:
    """Keep every probe window for the Live dashboard (when the probe runs without `adviewer probe run`)."""
    import threading

    from adviewer import project, recorder

    if dump is None:
        cfg = project.load()
        dump = cfg.path(cfg.live_dump)
    history = history or recorder.history_path_for(dump)
    typer.echo(f"watching {dump} → {history} (Ctrl+C stops)")

    def show(line: dict[str, object]) -> None:
        res = line.get("res") or {}
        cpu = f" · CPU {res['cpu']:.0f} %" if isinstance(res, dict) and res.get("cpu") is not None else ""
        typer.echo(f"  {str(line['t'])[11:19]} · {len(line['nodes'])} nodes · {len(line['topics'])} topics{cpu}")  # type: ignore[arg-type]

    stop = threading.Event()
    try:
        recorder.record(dump, history, stop, on_window=show,
                        on_wait=lambda: typer.echo("  another recorder keeps this history; waiting to take over"))
    except KeyboardInterrupt:
        stop.set()


@app.command("map")
def map_target(
    graph: Path = typer.Argument(..., exists=True),
    target: Path = typer.Argument(..., exists=True, help="target.yaml (template: target.example.yaml from `adviewer init`)"),
    markdown: Path | None = typer.Option(None, "--markdown", help="write the checklist here"),
    json_out: Path | None = typer.Option(None, "--json", help="write the full report here"),
) -> None:
    """Map a simulator/hardware descriptor onto Autoware's required inputs (Phase 5)."""
    import json

    import yaml

    from adviewer.analysis.mapping import mapping_report, to_markdown
    from adviewer.ingest.release_data import available_releases
    from adviewer.model import Graph

    g = Graph.load(graph)
    release = next((r for r in available_releases() if r.release == g.meta.autoware_release), None)
    subsystems = ((release.load("subsystems.yaml") or {}).get("subsystems") or {}) if release else {}
    report = mapping_report(g, yaml.safe_load(target.read_text(encoding="utf-8")) or {}, subsystems)
    typer.echo(f"coverage {report['coverage'] * 100:.0f} % of {report['required_count']} required inputs")
    for p in report["pairs"]:
        typer.echo(f"  {p['signal']} → {p['topic']}  (cost {p['cost']}: {p['terms']})")
    for name, r in report["readiness"].items():
        value = "n/a" if r["readiness"] is None else f"{r['readiness'] * 100:.0f} %"
        typer.echo(f"  {name} ready: {value}")
    if markdown:
        markdown.write_text(to_markdown(report, g), encoding="utf-8")
        typer.echo(f"wrote {markdown}")
    if json_out:
        json_out.write_text(json.dumps(report, indent=1), encoding="utf-8")
        typer.echo(f"wrote {json_out}")


@app.command()
def doctor(
    online: bool = typer.Option(False, "--online", help="also require what Online mode needs (probe image …)"),
) -> None:
    """Check Docker, the Autoware image, ROS 2 / DDS settings and kernel buffers (FR-12)."""
    from adviewer.doctor import run_doctor

    report = run_doctor(online=online)
    marks = {"ok": "✓", "warn": "!", "error": "✗", "info": "·"}
    for c in report["checks"]:
        typer.echo(f" {marks[c['status']]} {c['name']:<28} {c['detail']}")
        if c["fix"]:
            typer.echo(f"   fix: {c['fix']}")
    typer.echo(f"overall: {report['status']}")
    if report["status"] == "error":
        raise typer.Exit(code=1)


@app.command()
def warnings(graph: Path = typer.Argument(Path("graph.json"), exists=True)) -> None:
    """Print ingest warnings (unparsed launch files, unresolved includes, unknown directions)."""
    from adviewer.model import Graph

    for w in Graph.load(graph).meta.warnings:
        typer.echo(w)


@app.command()
def external(graph: Path = typer.Argument(Path("graph.json"), exists=True)) -> None:
    """List topics a sim/hardware bridge must provide (subscribed, never published)."""
    from adviewer.model import Graph

    for t in Graph.load(graph).external_inputs():
        typer.echo(t)


@app.command()
def annotate(
    graph: Path = typer.Argument(..., exists=True),
    probe_dump: Path = typer.Argument(..., exists=True),
    out: Path = typer.Option(Path("graph.live.json"), "--out"),
) -> None:
    """Merge a live probe dump into a graph: live edges, rates, health, mismatches (FR-9..11)."""
    import json

    from adviewer.analysis.live import annotate as merge
    from adviewer.model import Graph

    g = Graph.load(graph)
    report = merge(g, json.loads(probe_dump.read_text(encoding="utf-8")))
    g.save(out)
    typer.echo(f"wrote {out}; health {report['health']}")
    for key in ("missing_topics", "type_mismatches", "qos_incompatible", "below_rate"):
        typer.echo(f"  {key}: {len(report[key])}")
        for item in report[key][:10]:
            typer.echo(f"    {item}")


@app.command()
def compare(
    baseline: Path = typer.Argument(..., exists=True),
    loaded: Path = typer.Argument(..., exists=True),
    drop: float = typer.Option(0.1, help="relative rate drop that counts (0.1 = 10 %)"),
) -> None:
    """Rate drops between two probe dumps, related to the added load (FR-10)."""
    import json

    from adviewer.analysis.live import compare as diff

    result = diff(json.loads(baseline.read_text()), json.loads(loaded.read_text()), drop)
    typer.echo(result["summary"])
    for d in result["drops"]:
        typer.echo(f"  ↓ {d['topic']}: {d['before_hz']} → {d['after_hz']} Hz (-{d['drop'] * 100:.0f} %)")
    for t in result["added_topics"][:20]:
        typer.echo(f"  + {t}")


MISSING_TOOL = {
    "docker": "Docker is not installed: https://docs.docker.com/engine/install/ "
              "(Offline mode works without it: `adviewer init --from-source`)",
    "git": "git is not installed (Ubuntu: sudo apt install git; Windows: https://git-scm.com)",
}


def main() -> None:
    """The `adviewer` command: problems the user can fix end with one line, not a traceback."""
    try:
        app()
    except FileNotFoundError as exc:  # no adviewer.yaml here, or docker / git not installed
        tool = Path(str(exc.filename)).name if exc.filename else ""
        typer.echo(f"adviewer: {MISSING_TOOL.get(tool, str(exc))}", err=True)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
