"""Record every probe window into a history file (Online mode, D31).

The probe rewrites its dump once per window (loop mode, atomic rename). The recorder watches
that file and appends one JSON line per window: the running nodes, the measured topics, the
live wiring when it changed (who publishes / reads what), and what the machine was doing (host CPU / memory from /proc, GPU from
`nvidia-smi`, per-container CPU / memory from `docker stats`). Whatever is not available on
this machine is simply left out. `analysis/timeline.py` turns the lines into the dashboard.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from adviewer.analysis.timeline import window_record, wiring_of

MAX_BYTES = 50_000_000  # ~1–2 h of a full Autoware at 5 s windows; the older half is kept as .1
LOCK_STALE = 5.0  # s: a lock not refreshed for this long belongs to a recorder that has stopped


def history_path_for(dump: Path) -> Path:
    """`probe_dump.json` → `probe_dump.history.jsonl` (next to it)."""
    return dump.with_name(dump.stem + ".history.jsonl")


Runner = Callable[[list[str]], str]


def _run(cmd: list[str]) -> str:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=8, check=True).stdout


class Sampler:
    """Machine load at the moment a window is recorded."""

    def __init__(self, proc: Path = Path("/proc"), run: Runner = _run,
                 which: Callable[[str], str | None] = shutil.which):
        self.proc = proc
        self.run = run
        self.gpu = bool(which("nvidia-smi"))
        self.docker = bool(which("docker"))
        self._cpu: tuple[int, int] | None = None

    def _cpu_percent(self) -> float | None:
        try:
            fields = [int(x) for x in (self.proc / "stat").read_text(encoding="utf-8").split("\n")[0].split()[1:]]
        except (OSError, ValueError, IndexError):
            return None
        idle, total = fields[3] + (fields[4] if len(fields) > 4 else 0), sum(fields[:8])  # guest is inside user
        prev, self._cpu = self._cpu, (idle, total)
        if prev is None or total == prev[1]:
            return None
        return round(100.0 * (1 - (idle - prev[0]) / (total - prev[1])), 1)

    def _mem_percent(self) -> float | None:
        try:
            info = {line.split(":")[0]: int(line.split()[1]) for line in
                    (self.proc / "meminfo").read_text(encoding="utf-8").splitlines() if ":" in line}
            return round(100.0 * (1 - info["MemAvailable"] / info["MemTotal"]), 1)
        except (OSError, KeyError, ValueError, IndexError, ZeroDivisionError):
            return None

    def _gpu(self) -> dict[str, float]:
        if not self.gpu:
            return {}
        try:
            rows = [r.split(",") for r in self.run(["nvidia-smi", "--query-gpu=utilization.gpu,memory.used,memory.total",
                                                     "--format=csv,noheader,nounits"]).splitlines() if r.strip()]
            util = [float(r[0]) for r in rows]
            mem = [100.0 * float(r[1]) / float(r[2]) for r in rows if float(r[2]) > 0]
            return {"gpu": round(sum(util) / len(util), 1), "gpu_mem": round(sum(mem) / len(mem), 1)} if util else {}
        except (OSError, subprocess.SubprocessError, ValueError, IndexError, ZeroDivisionError):
            self.gpu = False  # do not retry every window
            return {}

    def _containers(self) -> dict[str, dict[str, float]]:
        if not self.docker:
            return {}
        try:
            out = self.run(["docker", "stats", "--no-stream", "--format", "{{json .}}"])
        except (OSError, subprocess.SubprocessError):
            self.docker = False
            return {}
        rows: dict[str, dict[str, float]] = {}
        for line in out.splitlines():
            try:
                s = json.loads(line)
                rows[s["Name"]] = {"cpu": float(s["CPUPerc"].rstrip("%")), "mem_MB": _megabytes(s["MemUsage"].split("/")[0])}
            except (ValueError, KeyError, AttributeError):
                continue
        return rows

    def sample(self) -> dict[str, Any]:
        res: dict[str, Any] = {"cpu": self._cpu_percent(), "mem": self._mem_percent(), **self._gpu()}
        containers = self._containers()
        if containers:
            res["containers"] = containers
        return {k: v for k, v in res.items() if v is not None}


def _megabytes(text: str) -> float:
    text = text.strip()
    for unit, factor in (("GiB", 1024.0), ("MiB", 1.0), ("KiB", 1 / 1024), ("GB", 1000.0), ("MB", 1.0), ("kB", 0.001), ("B", 1e-6)):
        if text.endswith(unit):
            return round(float(text[: -len(unit)]) * factor, 1)
    return float(text)


def append(history: Path, line: dict[str, Any], max_bytes: int = MAX_BYTES) -> None:
    """Append one window; past `max_bytes` the file becomes `<name>.1` and a new one starts."""
    if history.exists() and history.stat().st_size > max_bytes:
        history.replace(history.with_name(history.name + ".1"))
    with history.open("a", encoding="utf-8") as f:
        f.write(json.dumps(line, separators=(",", ":")) + "\n")


def read_history(history: Path, last: int = 360) -> list[dict[str, Any]]:
    """The last `last` windows, oldest first (also from the rotated `.1` file if needed)."""
    lines: list[str] = []
    for path in (history, history.with_name(history.name + ".1")):
        if len(lines) >= last or not path.is_file():
            continue
        lines = _tail(path, last - len(lines)) + lines
    out = []
    for line in lines[-last:]:
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue  # a line being written right now
    return out


def _tail(path: Path, n: int, block: int = 1 << 16) -> list[str]:
    with path.open("rb") as f:
        f.seek(0, 2)
        end = f.tell()
        data = b""
        while end > 0 and data.count(b"\n") <= n:
            start = max(0, end - block)
            f.seek(start)
            data = f.read(end - start) + data
            end = start
    return [ln.decode("utf-8", "replace") for ln in data.splitlines() if ln.strip()][-n:]


class Lock:
    """One recorder per history file, whoever runs (`probe run`, `serve --live`, `record`): a lock
    file holding the owner's token, its modification time the owner's heartbeat. Portable (no
    flock on Windows); another recorder takes over once the heartbeat is older than LOCK_STALE."""

    def __init__(self, history: Path):
        self.path = history.with_name(history.name + ".lock")
        self.token = uuid.uuid4().hex
        self.held = False

    def _owner(self) -> tuple[str, float]:
        try:
            return self.path.read_text(encoding="utf-8").strip(), self.path.stat().st_mtime
        except OSError:
            return "", 0.0

    def acquire(self) -> bool:
        """Call every poll: takes a free or stale lock, refreshes our own; True while we own it."""
        owner, mtime = self._owner()
        if owner == self.token:
            os.utime(self.path)
        elif not owner or time.time() - mtime > LOCK_STALE:
            if owner and self._owner() == (owner, mtime):  # still the same stale owner: clear it
                self.path.unlink(missing_ok=True)
            try:  # create-if-absent is atomic: of two recorders starting together, one wins
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            except FileExistsError:
                pass
            else:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(self.token)
            owner = self._owner()[0]
        self.held = owner == self.token
        return self.held

    def release(self) -> None:
        if self._owner()[0] == self.token:
            self.path.unlink(missing_ok=True)
        self.held = False


def record(dump: Path, history: Path, stop: threading.Event, sampler: Sampler | None = None,
           poll: float = 0.5, max_bytes: int = MAX_BYTES,
           on_window: Callable[[dict[str, Any]], None] | None = None, exclusive: bool = True,
           on_wait: Callable[[], None] | None = None) -> None:
    """Append a line for every new probe window until `stop` is set. With `exclusive`, only one
    recorder writes a history at a time; the others wait and take over when it stops."""
    sampler = sampler or Sampler()
    sampler.sample()  # first CPU reading: the next one is a difference
    history.parent.mkdir(parents=True, exist_ok=True)
    seen_mtime, seen_stamp = 0.0, ""
    wiring: dict[str, Any] | None = None  # written only when it changes (launches, stops)
    lock = Lock(history) if exclusive else None
    owned, waited = not exclusive, False
    try:
        while not stop.is_set():
            if lock is not None:
                was, owned = owned, lock.acquire()
                if not owned:
                    if not waited and on_wait:
                        on_wait()
                    waited = True
                    stop.wait(poll)
                    continue
                if not was:  # taking over: continue after the last recorded window
                    last = read_history(history, 1)
                    seen_stamp = str(last[0].get("t", "")) if last else seen_stamp
                    wiring = None  # the next line carries the full wiring again
            seen_mtime, seen_stamp, wiring = _poll(dump, history, sampler, max_bytes, on_window,
                                                   seen_mtime, seen_stamp, wiring)
            stop.wait(poll)
    finally:
        if lock is not None:
            lock.release()


def _poll(dump: Path, history: Path, sampler: Sampler, max_bytes: int,
          on_window: Callable[[dict[str, Any]], None] | None, seen_mtime: float, seen_stamp: str,
          wiring: dict[str, Any] | None) -> tuple[float, str, dict[str, Any] | None]:
    """Record the dump's window if it is new; returns the updated state."""
    try:
        mtime = dump.stat().st_mtime
    except OSError:
        return seen_mtime, seen_stamp, wiring
    if not mtime or mtime == seen_mtime:
        return seen_mtime, seen_stamp, wiring
    try:
        data = json.loads(dump.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return seen_mtime, seen_stamp, wiring  # replaced while reading: next poll
    if str(data.get("captured_at")) == seen_stamp:
        return mtime, seen_stamp, wiring
    last = read_history(history, 1)
    if last and str(last[0].get("t", "")) == str(data.get("captured_at")):  # in the history already
        return mtime, str(data.get("captured_at")), wiring
    line = window_record(data, sampler.sample())
    now = wiring_of(data)
    if now != wiring:
        line["wiring"], wiring = now, now
    append(history, line, max_bytes)
    if on_window:
        on_window(line)
    return mtime, str(data.get("captured_at")), wiring


def start(dump: Path, history: Path, on_window: Callable[[dict[str, Any]], None] | None = None,
          exclusive: bool = True) -> tuple[threading.Thread, threading.Event]:
    """Run `record` in a background thread; set the returned event to stop it."""
    stop = threading.Event()
    thread = threading.Thread(target=record, args=(dump, history, stop),
                              kwargs={"on_window": on_window, "exclusive": exclusive},
                              daemon=True, name="adviewer-recorder")
    thread.start()
    return thread, stop
