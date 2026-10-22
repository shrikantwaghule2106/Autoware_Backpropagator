"""Recorder (D31): machine load, history file with rotation, the watch loop, /api/timeline."""
import json
import os
import threading
import time
from pathlib import Path

from fastapi.testclient import TestClient

from adviewer.api.app import create_app
from adviewer.model import Graph
from adviewer.recorder import Sampler, append, history_path_for, read_history, record

DOCKER = '{"Name":"autoware","CPUPerc":"312.5%","MemUsage":"5.2GiB / 15.5GiB"}\n{"Name":"probe","CPUPerc":"1.0%","MemUsage":"40MiB / 15.5GiB"}\n'


def _proc(root: Path, busy: int, idle: int) -> Path:
    root.mkdir(exist_ok=True)
    (root / "stat").write_text(f"cpu  {busy} 0 0 {idle} 0 0 0 0 0 0\ncpu0 1 2 3 4\n", encoding="utf-8")
    (root / "meminfo").write_text("MemTotal:       16000000 kB\nMemAvailable:    4000000 kB\n", encoding="utf-8")
    return root


def test_sampler_cpu_memory_gpu_containers(tmp_path: Path) -> None:
    proc = _proc(tmp_path / "proc", 100, 900)
    outputs = {"nvidia-smi": "60, 2048, 8192\n40, 1024, 8192\n", "docker": DOCKER}
    s = Sampler(proc, run=lambda cmd: outputs[cmd[0]], which=lambda name: f"/usr/bin/{name}")
    first = s.sample()
    assert "cpu" not in first and first["mem"] == 75.0  # CPU needs two readings
    _proc(proc, 400, 1000)  # +300 busy, +100 idle → 75 %
    res = s.sample()
    assert res["cpu"] == 75.0 and res["gpu"] == 50.0 and res["gpu_mem"] == 18.8
    assert res["containers"]["autoware"] == {"cpu": 312.5, "mem_MB": 5324.8}
    bare = Sampler(tmp_path / "none", run=lambda cmd: "", which=lambda name: None).sample()
    assert bare == {}  # nothing available (e.g. Windows): nothing recorded, no error


def test_append_rotates_and_read_history_spans_both(tmp_path: Path) -> None:
    hist = tmp_path / "probe_dump.history.jsonl"
    assert history_path_for(tmp_path / "probe_dump.json") == hist
    for i in range(6):
        append(hist, {"t": str(i)}, max_bytes=30)  # each line ~10 bytes: rotates after 3
    assert (tmp_path / "probe_dump.history.jsonl.1").is_file()
    assert [w["t"] for w in read_history(hist, last=5)] == ["1", "2", "3", "4", "5"]
    with hist.open("a", encoding="utf-8") as f:
        f.write('{"t": "half')  # being written right now
    assert read_history(hist, last=2)[-1]["t"] == "5"


def _dump(path: Path, stamp: str, when: float) -> None:
    path.write_text(json.dumps({"captured_at": stamp, "window_s": 5.0, "nodes": ["/a"],
                                "topics": [{"name": "/x", "measured": True, "rate_hz": 10.0, "count": 50,
                                            "publishers": [{"node": "/a", "qos": {}}]}]}), encoding="utf-8")
    os.utime(path, (when, when))


def _wait(cond, seconds: float = 5.0) -> bool:
    end = time.time() + seconds
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


def test_record_appends_each_new_window_once(tmp_path: Path) -> None:
    dump, hist = tmp_path / "probe_dump.json", tmp_path / "probe_dump.history.jsonl"
    _dump(dump, "2026-10-17T13:40:00Z", 1000.0)
    stop = threading.Event()
    sampler = Sampler(_proc(tmp_path / "proc", 1, 9), run=lambda cmd: "", which=lambda name: None)
    t = threading.Thread(target=record, args=(dump, hist, stop), kwargs={"sampler": sampler, "poll": 0.02})
    t.start()
    try:
        assert _wait(lambda: len(read_history(hist)) == 1)
        os.utime(dump, (1001.0, 1001.0))  # touched, same window: not recorded again
        _dump(dump, "2026-10-17T13:40:05Z", 1002.0)
        assert _wait(lambda: len(read_history(hist)) == 2)
    finally:
        stop.set()
        t.join(5)
    windows = read_history(hist)
    assert [w["t"] for w in windows] == ["2026-10-17T13:40:00Z", "2026-10-17T13:40:05Z"]
    assert windows[0]["wiring"] == {"pub": {"/x": ["/a"]}, "sub": {}} and "wiring" not in windows[1]  # unchanged
    assert windows[0]["topics"]["/x"][1] == 10.0 and windows[0]["nodes"] == ["/a"]


def test_timeline_endpoint(tmp_path: Path) -> None:
    dump = tmp_path / "probe_dump.json"
    _dump(dump, "2026-10-17T13:40:50Z", 1000.0)
    g = Graph()
    assert TestClient(create_app(g)).get("/api/timeline").json() == {"enabled": False}
    c = TestClient(create_app(g, live_path=dump))
    assert c.get("/api/timeline").json()["available"] is False  # no history yet
    hist = history_path_for(dump)
    for k in range(10):
        append(hist, {"t": f"2026-10-17T13:40:{k * 5:02d}Z", "window_s": 5.0, "nodes": ["/a"] + (["/b"] if k >= 5 else []),
                      "topics": {"/sensing/lidar/points": [50, 10.0 if k < 5 else 5.0, 1, 1, 1, 1]}, "res": {"cpu": 30.0}})
    body = c.get("/api/timeline", params={"last": 20}).json()
    assert body["available"] and body["summary"]["windows"] == 10
    assert body["changes"][0]["started"] == ["/b"] and body["changes"][0]["roots"][0]["topic"] == "/sensing/lidar/points"
    assert c.get("/api/timeline", params={"last": 5}).status_code == 422  # at least 10 windows


def test_one_recorder_per_history_and_takeover(tmp_path: Path) -> None:
    """`probe run`, `serve --live` and `record` may all run: one writes, the next takes over."""
    from adviewer.recorder import Lock

    dump, hist = tmp_path / "probe_dump.json", tmp_path / "probe_dump.history.jsonl"
    quiet = Sampler(_proc(tmp_path / "proc", 1, 9), run=lambda cmd: "", which=lambda name: None)
    stops = [threading.Event(), threading.Event()]
    waits: list[int] = []  # which recorder waits
    threads = [threading.Thread(target=record, args=(dump, hist, stop),
                                kwargs={"sampler": quiet, "poll": 0.02, "on_wait": lambda i=i: waits.append(i)})
               for i, stop in enumerate(stops)]
    _dump(dump, "2026-10-19T10:00:00Z", 1000.0)
    for th in threads:
        th.start()  # both at once: the lock is created atomically, so one writes and one waits
    try:
        assert _wait(lambda: len(read_history(hist)) == 1) and _wait(lambda: len(waits) == 1)
        _dump(dump, "2026-10-19T10:00:05Z", 1001.0)
        assert _wait(lambda: len(read_history(hist)) == 2)
        writer = 1 - waits[0]
        stops[writer].set()  # the writer stops (Ctrl+C): the waiting one takes over
        threads[writer].join(5)
        _dump(dump, "2026-10-19T10:00:10Z", 1002.0)
        assert _wait(lambda: len(read_history(hist)) == 3)
    finally:
        for stop in stops:
            stop.set()
        for th in threads:
            th.join(5)
    assert [w["t"][-9:] for w in read_history(hist)] == ["10:00:00Z", "10:00:05Z", "10:00:10Z"]  # no window twice
    assert not Lock(hist).path.exists()  # released


def test_stale_lock_is_taken_over(tmp_path: Path) -> None:
    from adviewer.recorder import Lock

    hist = tmp_path / "h.jsonl"
    lock = Lock(hist)
    lock.path.write_text("someone-else", encoding="utf-8")
    assert not lock.acquire()  # fresh: another recorder is alive
    os.utime(lock.path, (time.time() - 60, time.time() - 60))
    assert lock.acquire() and lock.path.read_text() == lock.token  # stale: taken over
    lock.release()
    assert not lock.path.exists()
