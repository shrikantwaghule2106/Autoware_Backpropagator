"""HTTP API used by the UI (Phase 2–5): each endpoint is a thin call into analysis/."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from adviewer.api.app import create_app
from adviewer.ingest import ingest_autoware_tree
from adviewer.model import Graph

FIX = Path(__file__).parent / "fixtures" / "params"


@pytest.fixture(scope="module")
def graph() -> Graph:
    g = ingest_autoware_tree(FIX, entry_launch=str(FIX / "top_pkg/launch/top.launch.xml"))
    g.meta.autoware_release = "1.9.0"  # use the 1.9.0 data files (aliases, mrm, subsystems)
    return g


@pytest.fixture
def client(graph: Graph, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    import adviewer.doctor

    monkeypatch.setattr(adviewer.doctor, "run_doctor", lambda: {"status": "ok", "checks": []})
    dump = tmp_path / "probe_dump.json"
    dump.write_text(json.dumps({"captured_at": "2026-10-16T12:00:00Z", "nodes": ["/planning/planner"],
                                "topics": [{"name": "/x", "types": ["std_msgs/msg/String"], "measured": True,
                                            "count": 50, "rate_hz": 10.0,
                                            "publishers": [{"node": "/planning/planner", "qos": {}}],
                                            "subscribers": []}]}))
    target = tmp_path / "target.yaml"
    target.write_text("target: t\nprovides:\n  - {name: speed, ros_type: std_msgs/msg/Float64}\n")
    return TestClient(create_app(graph, live_path=dump, target_path=target))


def test_meta_search_details(client: TestClient) -> None:
    meta = client.get("/api/meta").json()
    assert meta["release_data"] == "1.9.0" and meta["has_live"] and meta["has_target"]
    hits = client.get("/api/search", params={"q": "max_v"}).json()
    assert hits[0] == {"kind": "param", "id": "p:max_vel", "label": "max_vel",
                       "detail": "4.17 (param-file)", "score": 70}
    p = client.get("/api/details", params={"id": "p:max_vel"}).json()
    assert p["entries"][0]["evidence"] == "top_pkg/config/common.param.yaml:3"
    assert p["entries"][0]["routes"][0]["route"].startswith("launch argument")
    n = client.get("/api/details", params={"id": "n:/planning/planner"}).json()
    assert n["package"] == "node_pkg" and n["param_count"] == 3
    assert client.get("/api/details", params={"id": "t:/nope"}).status_code == 404


def test_expand_impact_mrm_doctor(client: TestClient) -> None:
    assert client.get("/api/expand", params={"id": "n:/planning/planner", "dir": "back"}).json()["nodes"] == []
    assert client.get("/api/expand", params={"id": "n:/nope"}).status_code == 404
    r = client.get("/api/impact", params={"id": "n:/planning/planner"}).json()
    assert r["affected_nodes"][0] == {"id": "/planning/planner", "hop": 0, "via": "stopped"}
    assert "mrm" in r
    assert client.get("/api/mrm").json()["unit"] == ""  # no aggregator in this fixture
    assert client.get("/api/doctor").json()["status"] == "ok"


def test_live_reload_and_mapping(client: TestClient) -> None:
    live = client.get("/api/live").json()
    assert live["available"] and live["health"] == {"t:/x": "healthy"}
    m = client.get("/api/mapping").json()
    assert m["target"] == "t" and "coverage" in m
    assert client.get("/api/mapping.md").text.startswith("# Bridge checklist — t")


def test_without_live_or_target(graph: Graph) -> None:
    c = TestClient(create_app(graph))
    assert c.get("/api/live").json() == {"enabled": False}
    assert c.get("/api/mapping").status_code == 404
    assert c.get("/").status_code == 200


def test_security_headers_host_check_and_token(graph: Graph) -> None:
    local = TestClient(create_app(graph, allowed_hosts={"localhost", "127.0.0.1", "testserver"}))
    r = local.get("/api/meta")
    assert r.status_code == 200 and "script-src 'self'" in r.headers["content-security-policy"]
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["cache-control"] == "no-store"
    assert local.get("/api/meta", headers={"host": "evil.example"}).status_code == 400  # DNS rebinding
    assert local.post("/api/meta").status_code == 405  # read-only
    exposed = TestClient(create_app(graph, token="s3cret"))
    assert exposed.get("/api/meta").status_code == 401
    assert exposed.get("/api/meta", headers={"x-adviewer-token": "s3cret"}).status_code == 200
    first = exposed.get("/", params={"token": "s3cret"})
    assert first.status_code == 200 and "adviewer_token" in first.headers["set-cookie"]
    assert exposed.get("/api/meta").status_code == 200  # the cookie now carries the token
