"""Using adviewer in your own project: init, up, extract, fetch-source (owner, 2026-10-16)."""
import json
import sys
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from adviewer import project
from adviewer.cli import app

FIX = Path(__file__).parent / "fixtures" / "params"
runner = CliRunner()


def test_init_writes_config_tasks_and_ignore(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".gitignore").write_text("build/\n")
    r = runner.invoke(app, ["init", "--install-space", str(FIX), "--entry-launch", "top_pkg/launch/top.launch.xml"])
    assert r.exit_code == 0, r.output
    cfg = project.load(tmp_path)
    assert cfg.path(cfg.install_space) == FIX and cfg.entry_launch == "top_pkg/launch/top.launch.xml"
    tasks = json.loads((tmp_path / ".vscode/tasks.json").read_text())
    assert tasks["tasks"][0]["label"] == "adviewer: up"
    assert ".adviewer/" in (tmp_path / ".gitignore").read_text()
    example = yaml.safe_load((tmp_path / "target.example.yaml").read_text())
    assert example["target"] and example["provides"]  # the Mapping template, shipped in the package
    again = runner.invoke(app, ["init"])
    assert "kept existing adviewer.yaml" in again.output  # never overwritten silently
    assert "kept existing target.example.yaml" in again.output


def test_up_ingest_only_reads_the_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    runner.invoke(app, ["init", "--install-space", str(FIX), "--source", "",
                        "--entry-launch", "top_pkg/launch/top.launch.xml"])
    (tmp_path / "sub").mkdir()
    monkeypatch.chdir(tmp_path / "sub")  # adviewer.yaml is found in a parent folder
    r = runner.invoke(app, ["up", "--ingest-only"])
    assert r.exit_code == 0, r.output
    assert (tmp_path / ".adviewer/graph.json").is_file() and "read 1 nodes" in r.output


def test_extract_and_fetch_commands(tmp_path: Path) -> None:
    cmd = project.extract_command("ghcr.io/autowarefoundation/autoware:universe-cuda-jazzy-1.9.0",
                                  tmp_path, "/opt/autoware")
    assert cmd[:3] == ["docker", "run", "--rm"] and "-path '*/share/*'" in cmd[-1]
    assert "COLCON_IGNORE" in cmd[-1]  # the root marker the real image has
    clones = project.fetch_source_commands(project.release("1.9.0"), tmp_path)
    assert len(clones) == 7 and clones[4][-3:] == [
        "0.52.0", "https://github.com/autowarefoundation/autoware_universe.git",
        str(tmp_path / "universe/autoware_universe")]
    assert clones[4][:5] == ["git", "-c", "advice.detachedHead=false", "-c", "core.longpaths=true"]


def test_up_without_config_explains(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    r = runner.invoke(app, ["up"])
    assert r.exit_code != 0 and "adviewer init" in str(r.exception)


MINI = Path(__file__).parent / "fixtures" / "mini_autoware"


@pytest.mark.skipif(sys.platform == "win32", reason="runs the image's sh script on this machine")
def test_install_space_found_in_merged_and_per_package_layouts(tmp_path: Path) -> None:
    import subprocess

    (tmp_path / "merged" / "share" / "pkg").mkdir(parents=True)
    (tmp_path / "isolated" / "pkg" / "share" / "pkg").mkdir(parents=True)  # colcon's default
    (tmp_path / "isolated" / "setup.bash").write_text("", encoding="utf-8")
    (tmp_path / "plain").mkdir()
    names = [str(tmp_path / d) for d in ("merged", "isolated", "plain", "missing")]
    out = subprocess.run(["sh", "-c", project.detect_prefix_script(names)], capture_output=True, text=True, check=True)
    assert out.stdout.split() == names[:2]


def test_existing_clone_is_checked(tmp_path: Path) -> None:
    import subprocess

    repo = tmp_path / "repo"
    repo.mkdir()
    git = ["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t"]
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run([*git, "commit", "-q", "--allow-empty", "-m", "x"], check=True)
    subprocess.run([*git, "tag", "1.9.0"], check=True)
    assert project.clone_problem(repo, "1.9.0") == ""
    assert "not 2.0.0" in project.clone_problem(repo, "2.0.0")
    (tmp_path / "copied").mkdir()
    assert "not a git clone" in project.clone_problem(tmp_path / "copied", "1.9.0")


def test_init_from_source_reads_the_source_tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    r = runner.invoke(app, ["init", "--from-source", "--source", str(MINI),
                            "--entry-launch", "top_launch/share/top_launch/launch/autoware.launch.xml"])
    assert r.exit_code == 0 and "fetch-source, then adviewer up" in r.output, r.output
    aw = yaml.safe_load((tmp_path / "adviewer.yaml").read_text())["autoware"]
    assert aw["install_space"] == aw["source"] == str(MINI)
    r = runner.invoke(app, ["up", "--ingest-only"])
    assert r.exit_code == 0 and "read " in r.output, r.output
    g = json.loads((tmp_path / ".adviewer" / "graph.json").read_text())
    assert g["meta"]["entry_launch"] == "launch/top/launch/autoware.launch.xml"  # found by package


def test_up_refuses_an_empty_install_space_and_rereads_an_empty_graph(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from adviewer import cli

    monkeypatch.chdir(tmp_path)
    (tmp_path / "autoware" / "install").mkdir(parents=True)  # what an `extract` that stopped early leaves
    runner.invoke(app, ["init"])
    r = runner.invoke(app, ["up"])
    assert r.exit_code == 1 and "no Autoware nodes found" in r.output and "--from-source" in r.output
    assert not (tmp_path / ".adviewer" / "graph.json").exists()  # nothing saved to be served next time
    graph = tmp_path / ".adviewer" / "graph.json"
    graph.parent.mkdir()
    graph.write_text(json.dumps({"nodes": {}}), encoding="utf-8")  # saved by an older adviewer
    assert cli._graph_outdated(graph, []) == "the saved graph is empty"
    full = tmp_path / "full.json"
    full.write_text(json.dumps({"nodes": {"/a": {}}}), encoding="utf-8")
    assert cli._graph_outdated(full, [tmp_path / "missing"]) == ""
    import os
    os.utime(tmp_path / "adviewer.yaml", (full.stat().st_mtime + 10,) * 2)
    assert cli._graph_outdated(full, [tmp_path / "adviewer.yaml"]) == "adviewer.yaml changed since the graph was made"


def test_extract_checks_docker_and_disk_before_downloading(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import shutil
    import subprocess
    from types import SimpleNamespace

    from adviewer import cli, doctor

    monkeypatch.chdir(tmp_path)
    runner.invoke(app, ["init"])
    monkeypatch.setattr(doctor, "check_docker", lambda *a, **k: [doctor._check("docker", "error", "docker is not installed", "install it")])
    r = runner.invoke(app, ["extract"])
    assert r.exit_code == 2 and "needs Docker" in r.output and "--from-source" in r.output
    monkeypatch.setattr(doctor, "check_docker", lambda *a, **k: [doctor._check("docker", "ok", "29")])
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=1, stdout="", stderr=""))
    monkeypatch.setattr(shutil, "disk_usage", lambda _p: SimpleNamespace(free=8e9))
    monkeypatch.setattr(cli, "_docker_root", lambda: str(tmp_path))
    pulled: list[list[str]] = []
    monkeypatch.setattr(project, "run", lambda cmd: pulled.append(cmd) or 0)
    r = runner.invoke(app, ["extract"])
    assert r.exit_code == 1 and "5.0 GB download, about 20 GB on disk; 8 GB free" in r.output, r.output
    assert "not enough disk space" in r.output and pulled == []  # nothing downloaded
    r = runner.invoke(app, ["extract", "--yes"])
    assert pulled and pulled[0][:2] == ["docker", "pull"]


def test_problems_end_with_one_line_not_a_traceback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                   capsys: pytest.CaptureFixture[str]) -> None:
    from adviewer import cli

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["adviewer", "extract"])
    with pytest.raises(SystemExit) as done:
        cli.main()
    assert done.value.code == 1 and "run `adviewer init` first" in capsys.readouterr().err

    def no_docker() -> None:
        raise FileNotFoundError(2, "No such file or directory", "docker")
    monkeypatch.setattr(cli, "app", no_docker)
    with pytest.raises(SystemExit):
        cli.main()
    assert "Docker is not installed" in capsys.readouterr().err


def test_probe_hint_when_it_sees_no_topics() -> None:
    from adviewer.cli import _empty_hint

    assert _empty_hint({"topics": [{"name": "/a"}]}, None, False, []) == ""
    hint = _empty_hint({"topics": []}, None, False, ["stack-autoware-1"])
    assert "--like <container>" in hint and "stack-autoware-1" in hint
    assert "is Autoware running there" in _empty_hint({}, "aw", False, [])
    assert "ROS_DOMAIN_ID" in _empty_hint({}, None, True, [])


def test_releases_are_ordered_by_version_and_detected_by_package_versions() -> None:
    from adviewer.ingest.package_xml import PackageInfo
    from adviewer.ingest.release_data import available_releases, detect_release, version_key
    from adviewer.model import Layer

    assert sorted(["1.10.0", "1.9.0", "1.8.0"], key=version_key) == ["1.8.0", "1.9.0", "1.10.0"]  # not as text
    names = [r.release for r in available_releases()]
    assert names == sorted(names, key=version_key) and {"1.8.0", "1.9.0"} <= set(names)
    assert project.release(None).release == names[-1]  # the default: the newest adviewer has data for
    for release, launch in (("1.8.0", "0.51.0"), ("1.9.0", "0.52.0")):
        pkgs = {"autoware_launch": PackageInfo("autoware_launch", Path("x"), Layer.unknown, "x", launch)}
        found = detect_release(pkgs)
        assert found is not None and found.release == release
    assert detect_release({"autoware_launch": PackageInfo("autoware_launch", Path("x"), Layer.unknown, "x", "0.1.0")}) is None


def test_init_release_is_used_by_fetch_source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                               capsys: pytest.CaptureFixture[str]) -> None:
    from adviewer import cli

    monkeypatch.chdir(tmp_path)
    r = runner.invoke(app, ["init", "--from-source", "--release", "1.8.0"])
    assert r.exit_code == 0 and "Autoware 1.8.0" in r.output, r.output
    assert yaml.safe_load((tmp_path / "adviewer.yaml").read_text())["autoware"]["release"] == "1.8.0"
    clones: list[list[str]] = []
    monkeypatch.setattr(project, "run", lambda cmd: clones.append(cmd) or 0)
    r = runner.invoke(app, ["fetch-source"])
    assert r.exit_code == 0 and "Autoware 1.8.0" in r.output, r.output
    universe = next(c for c in clones if c[-1].endswith("autoware_universe"))
    assert universe[universe.index("--branch") + 1] == "0.51.0"  # 1.8.0's tag, not the newest release's
    monkeypatch.setattr(sys, "argv", ["adviewer", "init", "--release", "0.1.0", "--force"])
    with pytest.raises(SystemExit) as done:
        cli.main()
    err = capsys.readouterr().err
    assert done.value.code == 1 and "no data for Autoware 0.1.0; it knows 1.8.0, 1.9.0" in err, err
