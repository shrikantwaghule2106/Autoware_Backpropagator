"""Use adviewer inside your own project (VS Code, D17): a small config file, VS Code tasks,
and the two steps that get Autoware's files onto disk.

  adviewer init           writes adviewer.yaml (+ .vscode/tasks.json if absent)
  adviewer extract        copies the install space's text files out of the Autoware image
  adviewer fetch-source   shallow-clones the source companion at the release's tags
  adviewer up             ingests (when needed) and serves, using adviewer.yaml

Paths in adviewer.yaml are relative to the file itself, so the project folder can move.
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from adviewer.ingest.release_data import ReleaseData, available_releases

CONFIG = "adviewer.yaml"
TARGET_EXAMPLE = Path(__file__).resolve().parent / "data" / "target.example.yaml"
DEFAULT_ENTRY = "autoware_launch/share/autoware_launch/launch/autoware.launch.xml"
TEXT_FILES = ("*.xml", "*.launch.py", "*.yaml", "*.yml", "*.msg", "*.srv", "*.action", "*.json", "*.csv")


@dataclass
class Project:
    root: Path  # folder holding adviewer.yaml
    install_space: str = "autoware/install"
    source: str = "autoware/src"
    entry_launch: str = DEFAULT_ENTRY  # relative to install_space
    overlays: list[str] = field(default_factory=list)
    graph: str = ".adviewer/graph.json"
    host: str = "127.0.0.1"
    port: int = 8765
    live_dump: str = ".adviewer/probe_dump.json"
    target: str = ""

    def path(self, value: str) -> Path:
        p = Path(value).expanduser()
        return p if p.is_absolute() else (self.root / p)

    def to_yaml(self) -> str:
        data = {
            "autoware": {"install_space": self.install_space, "source": self.source,
                         "entry_launch": self.entry_launch, "overlays": self.overlays},
            "graph": self.graph,
            "serve": {"host": self.host, "port": self.port},
            "live": {"dump": self.live_dump},
            "target": self.target,
        }
        header = ("# adviewer project config (`adviewer init`). Paths are relative to this file.\n"
                  "# install_space: Autoware's install space (`adviewer extract` copies it from the image),\n"
                  "#   or the source itself (`adviewer init --from-source`: no Docker needed)\n"
                  "# source: the same release's source (`adviewer fetch-source`), optional but recommended\n"
                  "# target: your target.yaml for the Mapping panel (start from target.example.yaml)\n")
        return header + yaml.safe_dump(data, sort_keys=False)


def load(start: Path | None = None) -> Project:
    """The adviewer.yaml in `start` or the nearest parent folder."""
    here = (start or Path.cwd()).resolve()
    for folder in [here, *here.parents]:
        f = folder / CONFIG
        if f.is_file():
            data: dict[str, Any] = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
            aw = data.get("autoware") or {}
            serve = data.get("serve") or {}
            return Project(
                root=folder,
                install_space=str(aw.get("install_space", "autoware/install")),
                source=str(aw.get("source") or ""),
                entry_launch=str(aw.get("entry_launch", DEFAULT_ENTRY)),
                overlays=[str(o) for o in aw.get("overlays") or []],
                graph=str(data.get("graph", ".adviewer/graph.json")),
                host=str(serve.get("host", "127.0.0.1")),
                port=int(serve.get("port", 8765)),
                live_dump=str((data.get("live") or {}).get("dump", ".adviewer/probe_dump.json")),
                target=str(data.get("target") or ""),
            )
    raise FileNotFoundError(f"no {CONFIG} here or in a parent folder — run `adviewer init` first")


VSCODE_TASKS = {
    "version": "2.0.0",
    "tasks": [
        {"label": "adviewer: up", "type": "shell", "command": "adviewer up", "isBackground": True,
         "problemMatcher": {"pattern": {"regexp": "^__never__$"},
                            "background": {"activesBegin": True, "beginsPattern": "adviewer:",
                                           "endsPattern": "Uvicorn running"}},
         "detail": "Ingest (if needed) and serve; then Ctrl+Shift+P → Simple Browser: Show → the URL"},
        {"label": "adviewer: ingest", "type": "shell", "command": "adviewer up --ingest-only",
         "detail": "Re-read Autoware's files into the graph"},
        {"label": "adviewer: doctor", "type": "shell", "command": "adviewer doctor",
         "detail": "Check Docker, the Autoware image, ROS 2 / DDS settings"},
        {"label": "adviewer: doctor (online)", "type": "shell", "command": "adviewer doctor --online",
         "detail": "Everything Online mode needs"},
    ],
}


def init(folder: Path, install_space: str, source: str, entry_launch: str, force: bool) -> list[str]:
    """Write adviewer.yaml and VS Code tasks; returns what was done (never overwrites without force)."""
    done: list[str] = []
    cfg = folder / CONFIG
    if cfg.exists() and not force:
        done.append(f"kept existing {CONFIG} (use --force to replace it)")
    else:
        cfg.write_text(Project(folder, install_space, source, entry_launch).to_yaml(), encoding="utf-8")
        done.append(f"wrote {CONFIG}")
    tasks = folder / ".vscode" / "tasks.json"
    if tasks.exists():
        done.append(".vscode/tasks.json exists — add the adviewer tasks from `adviewer init --print-tasks`")
    else:
        tasks.parent.mkdir(exist_ok=True)
        tasks.write_text(json.dumps(VSCODE_TASKS, indent=2) + "\n", encoding="utf-8")
        done.append("wrote .vscode/tasks.json (Terminal → Run Task → adviewer: up)")
    example = folder / TARGET_EXAMPLE.name
    if example.exists():
        done.append(f"kept existing {example.name}")
    else:
        example.write_text(TARGET_EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
        done.append(f"wrote {example.name} (Mapping panel: copy it to target.yaml and describe "
                    "your simulator or hardware, then set `target: target.yaml` in adviewer.yaml)")
    ignore = folder / ".gitignore"
    if ignore.is_file() and ".adviewer/" not in ignore.read_text(encoding="utf-8").split():
        with ignore.open("a", encoding="utf-8") as f:
            f.write("\n# adviewer: generated graph and probe dumps\n.adviewer/\n")
        done.append("added .adviewer/ to .gitignore")
    return done


def release(name: str | None) -> ReleaseData:
    releases = available_releases()
    if not releases:
        raise RuntimeError("no release data in adviewer/data")
    if name is None:
        return releases[-1]
    for r in releases:
        if r.release == name:
            return r
    raise RuntimeError(f"no data for Autoware {name}; known: {', '.join(r.release for r in releases)}")


def extract_command(image: str, out: Path, prefix: str) -> list[str]:
    """docker command that copies the install space's text files (launch, config, msg,
    package.xml) out of an image — about 27 MB for 1.9.0 instead of the whole image."""
    names = " -o ".join(f"-name '{p}'" for p in TEXT_FILES)
    script = (f"cd {shlex.quote(prefix)} && find . -path '*/share/*' -type f \\( {names} \\) -print0 "
              f"| tar --null -cf - -T - | tar -xf - -C /out && touch /out/COLCON_IGNORE")
    user: list[str] = []
    if sys.platform != "win32":  # mypy understands this check; Windows has no uid/gid
        user = ["--user", f"{os.getuid()}:{os.getgid()}"]  # files owned by you
    return ["docker", "run", "--rm", *user, "--entrypoint", "/bin/bash",
            "-v", f"{out.resolve()}:/out", image, "-c", script]


PREFIXES = ["/opt/autoware", "/autoware/install", "/opt/autoware/install", "/autoware/install-space"]


def detect_prefix_script(candidates: list[str]) -> str:
    """sh script printing the candidates that are install spaces: merged (<prefix>/share/) or
    colcon's default isolated layout (<prefix>/<pkg>/share/, with setup.bash at the top)."""
    names = " ".join(shlex.quote(p) for p in dict.fromkeys(candidates))
    return (f'for d in {names}; do if [ -d "$d/share" ] || [ -f "$d/setup.bash" ] '
            f'|| ls -d "$d"/*/share >/dev/null 2>&1; then echo "$d"; fi; done; true')


def detect_prefix_command(image: str, first: str) -> list[str]:
    """docker command that prints the image's install spaces, `first` first."""
    return ["docker", "run", "--rm", "--entrypoint", "sh", image, "-c", detect_prefix_script([first, *PREFIXES])]


def fetch_source_commands(rel: ReleaseData, out: Path) -> list[list[str]]:
    """Shallow clones at the release's tags (the path last); quiet about the detached HEAD a tag gives,
    and with long paths allowed (Autoware's tree is deeper than Windows' 260 characters)."""
    repos = (rel.load("release.yaml") or {}).get("source_repos") or []
    return [["git", "-c", "advice.detachedHead=false", "-c", "core.longpaths=true", "clone", "--depth", "1",
             "--branch", str(r["version"]), str(r["url"]), str(out / r["path"])] for r in repos]


def clone_problem(folder: Path, version: str) -> str:
    """Why an existing folder is not the clone `fetch-source` would make ("" when it is)."""
    if not (folder / ".git").exists():
        return f"{folder} exists but is not a git clone: move it away and run `adviewer fetch-source` again"
    try:
        tag = subprocess.run(["git", "-C", str(folder), "describe", "--tags", "--exact-match", "HEAD"],
                             capture_output=True, text=True, check=False, timeout=30).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        tag = ""
    if tag != version:
        return (f"{folder} is at {tag or 'another commit'}, not {version}: move it away and run "
                "`adviewer fetch-source` again, or keep it if you chose that version on purpose")
    return ""


def run(cmd: list[str]) -> int:
    print("$", " ".join(shlex.quote(c) for c in cmd), flush=True)
    return subprocess.call(cmd)
