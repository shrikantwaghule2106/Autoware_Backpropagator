"""Online mode: annotate (FR-9, FR-11) and compare (FR-10) on a dump in the probe's format."""
from adviewer.analysis.live import annotate, compare
from adviewer.model import Confidence, Direction, Edge, Graph, Rate

QOS_REL = {"reliability": "reliable", "durability": "volatile", "history": "unknown", "depth": 0}
QOS_BE = {**QOS_REL, "reliability": "best_effort"}


def _graph() -> Graph:
    g = Graph()
    for node, topic, d in [("/lidar/driver", "/lidar/points", Direction.publish),
                           ("/perception/seg", "/lidar/points", Direction.subscribe),
                           ("/planning/planner", "/planning/trajectory", Direction.publish)]:
        g.add_edge(Edge(node=node, topic=topic, direction=d, confidence=Confidence.static_source,
                        evidence="a.launch.xml:3"))
    g.topics["/lidar/points"].msg_type = "sensor_msgs/msg/PointCloud2"
    g.topics["/lidar/points"].rates.append(
        Rate(hz=5.0, kind="monitor", evidence="topics.yaml:9", note="m: warn below 5.0 Hz, error below 1.0 Hz, timeout 1.0 s"))
    return g


def _topic(name, rate, pubs, subs=(), types=("sensor_msgs/msg/PointCloud2",), count=None, bw=1e6):
    return {"name": name, "types": list(types), "measured": True, "rate_hz": rate,
            "count": count if count is not None else int(rate * 5), "jitter_ms": 1.0, "max_gap_ms": 200.0,
            "bandwidth_Bps": bw, "mean_size_B": 1000,
            "publishers": [{"node": n, "type": types[0], "qos": q} for n, q in pubs],
            "subscribers": [{"node": n, "type": types[0], "qos": q} for n, q in subs]}


def test_annotate_health_edges_and_mismatches() -> None:
    g = _graph()
    dump = {"captured_at": "2026-10-16T12:00:00Z", "window_s": 5.0,
            "nodes": ["/lidar/driver", "/perception/seg", "/camera/driver"],
            "topics": [_topic("/lidar/points", 3.0, [("/lidar/driver", QOS_BE)], [("/perception/seg", QOS_REL)]),
                       _topic("/camera/image", 30.0, [("/camera/driver", QOS_REL)], types=("sensor_msgs/msg/Image",))]}
    report = annotate(g, dump)
    t = g.topics["/lidar/points"]
    assert t.health == "degraded" and t.rate_hz == 3.0  # between error 1 Hz and warn 5 Hz
    assert any(r.kind == "live" for r in t.rates)
    e = next(e for e in g.edges if e.key == ("/lidar/driver", "/lidar/points", Direction.publish))
    assert e.confidence == Confidence.live and "static: a.launch.xml:3" in e.evidence
    assert report["qos_incompatible"][0]["reasons"] == ["publisher best_effort, subscriber requires reliable"]
    assert report["below_rate"] == [{"topic": "/lidar/points", "rate_hz": 3.0, "expected_hz": 5.0}]
    assert report["live_only_topics"] == ["/camera/image"] and g.topics["/camera/image"].health == "healthy"
    assert report["missing_topics"][0]["topic"] == "/planning/trajectory"
    assert g.meta.live_captured_at == "2026-10-16T12:00:00Z"


def test_health_rules() -> None:
    g = _graph()
    annotate(g, {"topics": [_topic("/lidar/points", 0.5, [("/lidar/driver", QOS_BE)], count=3)]})
    assert g.topics["/lidar/points"].health == "unhealthy"
    g = _graph()
    annotate(g, {"topics": [_topic("/lidar/points", 10.0, [("/lidar/driver", QOS_BE)])]})
    assert g.topics["/lidar/points"].health == "healthy"
    g = _graph()
    annotate(g, {"topics": [_topic("/planning/trajectory", 0.0, [("/planning/planner", QOS_REL)], count=0,
                                   types=("x/msg/T",))]})
    assert g.topics["/planning/trajectory"].health == "idle"  # no threshold, nothing sent


def test_type_mismatch() -> None:
    g = _graph()
    report = annotate(g, {"topics": [_topic("/lidar/points", 10.0, [("/lidar/driver", QOS_BE)], types=("x/msg/Wrong",))]})
    assert report["type_mismatches"][0]["expected"] == "sensor_msgs/msg/PointCloud2"


def test_compare_lidar_drop_when_cameras_added() -> None:  # the owner's FR-10 example
    base = {"topics": [_topic("/lidar/points", 10.0, [], bw=20e6)]}
    loaded = {"topics": [_topic("/lidar/points", 7.0, [], bw=14e6)]
              + [_topic(f"/camera{i}/image", 30.0, [], bw=25e6) for i in range(3)]}
    r = compare(base, loaded)
    assert r["drops"] == [{"topic": "/lidar/points", "before_hz": 10.0, "after_hz": 7.0, "drop": 0.3,
                           "max_gap_ms_after": 200.0}]
    assert len(r["added_topics"]) == 3 and "+75.00 MB/s" in r["summary"]
