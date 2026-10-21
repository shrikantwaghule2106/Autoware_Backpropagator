"""Live dashboard (D31): launch events, change log and the one-level answer, on synthetic windows."""
from adviewer.analysis.timeline import lane_of, timeline, window_record
from adviewer.model import Confidence, Direction, Edge, Graph, Rate

LIDAR, FILTERED, OBJECTS, IMAGE = ("/sensing/lidar/points", "/sensing/lidar/filtered",
                                   "/perception/objects", "/sensing/camera/image")
BASE = ["/lidar/driver", "/lidar/filter", "/perception/seg"]


def _graph() -> Graph:
    g = Graph()
    for node, topic, d in [("/lidar/driver", LIDAR, Direction.publish),
                           ("/lidar/filter", LIDAR, Direction.subscribe),
                           ("/lidar/filter", FILTERED, Direction.publish),
                           ("/perception/seg", FILTERED, Direction.subscribe),
                           ("/perception/seg", OBJECTS, Direction.publish),
                           ("/camera/driver", IMAGE, Direction.publish),
                           ("/lidar/relay", "/sensing/lidar/raw", Direction.subscribe),
                           ("/lidar/relay", LIDAR, Direction.publish),
                           ("/rviz", LIDAR, Direction.subscribe)]:
        g.add_edge(Edge(node=node, topic=topic, direction=d, confidence=Confidence.static_source, evidence="a.launch.xml:1"))
    g.topics[OBJECTS].rates.append(Rate(hz=5.0, kind="monitor", evidence="m.yaml:3",
                                        note="m: warn below 5.0 Hz, error below 1.0 Hz, timeout 1.0 s"))
    return g


def _w(k: int, nodes: list[str], rates: dict[str, float], cpu: float = 50.0) -> dict:
    return {"t": f"2026-10-17T13:40:{k * 5:02d}Z", "window_s": 5.0, "nodes": nodes,
            "topics": {n: [int(r * 5), r, 1.0, 120.0, 1e6, 1] for n, r in rates.items()},
            "res": {"cpu": cpu, "mem": 40.0, "containers": {"autoware": {"cpu": cpu * 4, "mem_MB": 5000.0}}}}


def _launch_camera(drop_to: float = 7.0, n: int = 11, start: int = 6) -> list[dict]:
    out = []
    for k in range(n):
        on = k >= start
        nodes = BASE + (["/camera/driver"] if on else [])
        r = drop_to if on else 10.0
        rates = {LIDAR: r, FILTERED: r, OBJECTS: r} | ({IMAGE: 30.0} if on else {})
        out.append(_w(k, nodes, rates, cpu=95.0 if on else 50.0))
    return out


def test_launch_unconnected_root_followers_and_resources() -> None:
    tl = timeline(_graph(), _launch_camera())
    [c] = [c for c in tl["changes"] if c["status"] == "affected"]
    assert c["kind"] == "launch" and c["started"] == ["/camera/driver"] and c["i"] == 6
    [root] = c["roots"]
    assert root["topic"] == LIDAR and root["relation"] == "unconnected" and root["kind"] == "drop"
    assert (root["before_hz"], root["after_hz"]) == (10.0, 7.0)
    assert root["followers"] == [OBJECTS, FILTERED] or set(root["followers"]) == {FILTERED, OBJECTS}
    assert c["resources"]["cpu"] == [50.0, 95.0] and c["resources"]["containers"][0]["name"] == "autoware"
    assert IMAGE in c["added_topics"]
    assert "No topic connects it" in c["answer"] and "CPU 50 → 95 %" in c["answer"]
    assert "2 downstream topic(s) followed" in c["answer"]
    assert tl["summary"]["changes"] == 1 and tl["events"][0]["started"] == ["/camera/driver"]


def test_data_path_and_new_reader() -> None:
    def run(new: str) -> dict:
        ws = [_w(k, BASE + ([new] if k >= 4 else []), {LIDAR: 6.0 if k >= 4 else 10.0}) for k in range(9)]
        [c] = [c for c in timeline(_graph(), ws)["changes"] if c["status"] == "affected"]
        return c["roots"][0]

    relay = run("/lidar/relay")  # publishes the lidar topic
    assert relay["relation"] == "data path" and relay["node"] == "/lidar/relay" and relay["path"] == [LIDAR]
    rviz = run("/rviz")  # reads it
    assert rviz["relation"] == "new reader" and rviz["node"] == "/rviz"


def test_change_without_launch_event() -> None:
    ws = [_w(k, BASE, {LIDAR: 4.0 if k >= 8 else 10.0}) for k in range(12)]
    tl = timeline(_graph(), ws)
    [c] = tl["changes"]
    assert c["kind"] == "unexplained" and c["i"] == 8 and c["roots"][0]["topic"] == LIDAR
    assert c["answer"].startswith(f"At 13:40:40, {LIDAR} fell 10.0 → 4.0 Hz (−60 %) with no node started")
    assert "machine load stayed level" in c["answer"]


def test_discovery_jitter_is_not_an_event() -> None:
    ws = [_w(k, [n for n in BASE if not (k == 5 and n == "/perception/seg")], {LIDAR: 10.0}) for k in range(10)]
    assert timeline(_graph(), ws)["events"] == []


def test_measuring_and_no_effect() -> None:
    measuring = _launch_camera(drop_to=10.0, n=7, start=6)  # started in the last window
    [c] = timeline(_graph(), measuring)["changes"]
    assert c["status"] == "measuring" and "Measuring the effect" in c["answer"]
    calm = _launch_camera(drop_to=10.0, n=11, start=6)
    [c] = timeline(_graph(), calm)["changes"]
    assert c["status"] == "no effect" and c["answer"].endswith("No measured topic changed.")


def test_health_worse_without_a_large_drop() -> None:
    ws = [_w(k, BASE, {OBJECTS: 4.8 if k >= 5 else 5.2}) for k in range(10)]  # −8 %, but below warn 5 Hz
    [c] = timeline(_graph(), ws)["changes"]
    root = c["roots"][0]
    assert root["kind"] == "health" and root["health_before"] == "healthy" and root["health_after"] == "degraded"


def test_stopped_source() -> None:
    ws = [_w(k, BASE if k < 5 else ["/lidar/filter", "/perception/seg"], {LIDAR: 10.0} if k < 5 else {})
          for k in range(9)]
    [c] = [c for c in timeline(_graph(), ws)["changes"] if c["status"] == "affected"]
    root = c["roots"][0]
    assert c["kind"] == "stop" and root["kind"] == "stopped" and root["relation"] == "data path"
    assert "Its source /lidar/driver stopped" in c["answer"]


def test_lanes_and_window_record() -> None:
    assert [lane_of(t) for t in (LIDAR, IMAGE, OBJECTS, "/tf")] == ["lidar", "camera", "perception", "tf"]
    tl = timeline(_graph(), _launch_camera())
    names = [lane["name"] for lane in tl["lanes"]]
    assert names.index("lidar") < names.index("camera") < names.index("perception")
    lidar = next(lane for lane in tl["lanes"] if lane["name"] == "lidar")
    assert lidar["topics"][0]["changed"] and len(lidar["topics"][0]["rates"]) == 11
    dump = {"captured_at": "2026-10-17T13:40:00Z", "window_s": 5.0, "nodes": ["/b", "/a"],
            "topics": [{"name": "/x", "measured": True, "rate_hz": 9.87654, "count": 49, "jitter_ms": 1.2,
                        "max_gap_ms": 130.0, "bandwidth_Bps": 1234.5, "publishers": [{}]},
                       {"name": "/y", "measured": False}]}
    line = window_record(dump, {"cpu": 12.0})
    assert line["nodes"] == ["/a", "/b"] and line["topics"] == {"/x": [49, 9.877, 1.2, 130.0, 1234, 1]}
    assert line["res"] == {"cpu": 12.0} and timeline(_graph(), [])["summary"] == {"windows": 0}


def test_feedback_loop_has_one_root_earliest_in_the_pipeline() -> None:
    """lidar → localization pose → twist → back into the lidar concatenation (as in Autoware)."""
    g = Graph()
    for node, topic, d in [("/sensing/concat", "/sensing/lidar/concatenated", Direction.publish),
                           ("/sensing/concat", "/localization/twist", Direction.subscribe),
                           ("/localization/ndt", "/sensing/lidar/concatenated", Direction.subscribe),
                           ("/localization/ndt", "/localization/pose", Direction.publish),
                           ("/localization/ekf", "/localization/pose", Direction.subscribe),
                           ("/localization/ekf", "/localization/twist", Direction.publish),
                           ("/far/a", "/far/x", Direction.publish), ("/far/b", "/far/x", Direction.subscribe),
                           ("/far/b", "/far/y", Direction.publish), ("/localization/ekf", "/far/y", Direction.subscribe)]:
        g.add_edge(Edge(node=node, topic=topic, direction=d, confidence=Confidence.static_source, evidence="x:1"))
    loop = ["/sensing/lidar/concatenated", "/localization/pose", "/localization/twist"]
    ws = [_w(k, ["/sensing/concat", "/localization/ndt", "/localization/ekf", "/far/b"] + (["/far/a"] if k >= 5 else []),
             {t: 6.0 if k >= 5 else 10.0 for t in loop}) for k in range(10)]
    [c] = [c for c in timeline(g, ws)["changes"] if c["status"] == "affected"]
    [root] = c["roots"]
    assert root["topic"] == "/sensing/lidar/concatenated"  # sensing comes first in the loop
    assert set(root["followers"]) == {"/localization/pose", "/localization/twist"}
    # /far/a feeds the root along 4 topics (x → y → twist → lidar), more than near=3: not the data path
    assert root["relation"] == "unconnected"


def test_recorded_wiring_names_a_source_that_has_stopped() -> None:
    """Live-only nodes (no static edge): the wiring recorded before the stop is used."""
    ws = [_w(k, ["/bridge/lidar"] if k < 5 else [], {"/sim/lidar": 10.0} if k < 5 else {}) for k in range(9)]
    ws[0]["wiring"] = {"pub": {"/sim/lidar": ["/bridge/lidar"]}, "sub": {}}
    [c] = [c for c in timeline(Graph(), ws)["changes"] if c["status"] == "affected"]
    assert c["roots"][0]["relation"] == "data path" and "Its source /bridge/lidar stopped" in c["answer"]


def test_topic_that_publishes_on_change_is_not_stopped() -> None:
    """A speed limit published on change: quiet windows are normal; only a lost publisher counts."""
    limit = "/planning/max_velocity"
    burst = [0.4, 0.0, 0.0, 0.2, 0.0, 0.4, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
    ws = [_w(k, BASE, {LIDAR: 10.0}) for k in range(12)]
    for k, r in enumerate(burst):
        ws[k]["topics"][limit] = [int(r * 5), r, 0.0, 0.0, 10.0, 1]  # publisher stays, messages pause
    assert timeline(_graph(), ws)["changes"] == []
    for k in range(8, 12):
        ws[k]["topics"][limit] = [0, 0.0, 0.0, 0.0, 0.0, 0]  # now its publisher is gone
    [c] = timeline(_graph(), ws)["changes"]
    assert c["roots"][0]["topic"] == limit and c["roots"][0]["kind"] == "stopped"


def test_long_ranges_are_thinned_but_drops_stay_visible() -> None:
    from adviewer.analysis.timeline import downsample

    tl = timeline(_graph(), _launch_camera(n=30, start=12))
    small = downsample(tl, 10)
    assert small["bucket"] == 3 and len(small["windows"]) == 10
    lidar = next(t for lane in small["lanes"] for t in lane["topics"] if t["name"] == LIDAR)
    assert len(lidar["rates"]) == 10 and min(r for r in lidar["rates"] if r is not None) == 7.0  # the drop survives
    assert small["events"][0]["i"] == 12 // 3 and small["changes"][0]["i"] == tl["changes"][0]["i"] // 3
    assert downsample(tl, 300)["bucket"] == 1  # short ranges unchanged
