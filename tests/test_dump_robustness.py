"""Probe dumps from other writers (older/newer probes, hand-edited files, non-ROS DDS participants)
never crash annotate, compare, the recorder's history line or the timeline (fuzz, fixed seed)."""
import random

from adviewer.analysis.live import NON_ROS, annotate, compare, normalize_dump
from adviewer.analysis.timeline import timeline, window_record, wiring_of
from adviewer.model import Graph


def _val(r: random.Random):
    return r.choice([None, 0, -1, 3.5, 1e12, float("nan"), float("inf"), "7", "x", [], {}, True])


def _endpoint(r: random.Random) -> dict:
    e = {"node": r.choice(["/a", "/b", "", None, 5]), "type": r.choice(["x", None]), "qos": r.choice([{}, None, "q", {"reliability": "best_effort"}])}
    return {k: v for k, v in e.items() if r.random() > 0.2}


def _dump(r: random.Random, i: int):
    if r.random() < 0.03:
        return r.choice([None, [], "text", 5])
    topics = []
    for name in r.sample(["/x", "/y", "/sensing/lidar/p", "/z", "/x", "", None], k=r.randint(0, 5)):
        t = {"name": name, "types": r.choice([["pkg/msg/T"], [], ["a/msg/A", "b/msg/B"], None, "T"]),
             "measured": r.choice([True, False, None, "yes"]), "rate_hz": _val(r), "count": _val(r), "jitter_ms": _val(r),
             "max_gap_ms": _val(r), "bandwidth_Bps": _val(r), "mean_size_B": _val(r),
             "publishers": r.choice([[_endpoint(r) for _ in range(r.randint(0, 2))], None, [], "p", [None]]),
             "subscribers": r.choice([[_endpoint(r)], None, []])}
        topics.append({k: v for k, v in t.items() if k == "name" or r.random() > 0.15})
    d = {"captured_at": r.choice([f"2026-10-20T10:00:{i % 60:02d}Z", None, "", 5]), "window_s": _val(r),
         "nodes": r.choice([["/a", "/b"], [], None, ["/a", None, 3], "nodes"]), "topics": r.choice([topics, topics, None, "t"])}
    return {k: v for k, v in d.items() if r.random() > 0.1}


def test_fuzzed_dumps_never_crash() -> None:
    r = random.Random(20261020)
    lines = []
    for i in range(1500):
        d = _dump(r, i)
        annotate(Graph(), d)
        compare(d, _dump(r, i + 1))
        wiring_of(d)
        lines.append(window_record(d, {"cpu": _val(r), "containers": r.choice([{"c": {"cpu": _val(r)}}, None, "x"])}))
    lines += [None, [], {"t": "x"}, {"topics": {"/x": "bad"}}, {"topics": {"/x": [1, "a", None]}, "res": "r"}]
    for k in range(40):
        timeline(Graph(), r.sample(lines, k=min(len(lines), r.randint(0, 60))))


def test_non_ros_endpoints_keep_their_topic_published() -> None:
    dump = {"captured_at": "2026-10-20T10:00:00Z", "window_s": 5.0, "nodes": ["/a"],
            "topics": [{"name": "/sim/points", "types": ["sensor_msgs/msg/PointCloud2"], "measured": True, "rate_hz": 10.0,
                        "count": 50, "publishers": [{"node": "", "type": "x", "qos": {}}], "subscribers": []},
                       {"name": "/sim/points", "types": [], "measured": False}]}  # a duplicate entry
    clean = normalize_dump(dump)
    assert len(clean["topics"]) == 1 and clean["topics"][0]["publishers"][0]["node"] == NON_ROS
    g = Graph()
    annotate(g, dump)
    assert g.topics["/sim/points"].health == "healthy"  # a raw-DDS publisher (a simulator) still counts
    assert window_record(dump)["topics"]["/sim/points"][5] == 1
