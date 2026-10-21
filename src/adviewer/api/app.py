"""FastAPI app: serves JSON views of the graph and the UI. Thin by design — every answer
comes from a pure function in adviewer.analysis; this module only loads the release data
files, the optional live dump and target descriptor, and wires them to HTTP."""
from __future__ import annotations

import json
import secrets
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import yaml
from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from adviewer.analysis import criticality, drilldown
from adviewer.analysis.details import node_details, topic_about, topic_details
from adviewer.analysis.explore import expand, kit_nodes, search, trace
from adviewer.analysis.impact import mrm_gates, shutdown_impact
from adviewer.analysis.live import annotate
from adviewer.analysis.mapping import mapping_report, to_markdown
from adviewer.analysis.switches import list_switches, switch_nodes
from adviewer.analysis.timeline import downsample, timeline
from adviewer.analysis.where_to_edit import find_params
from adviewer.ingest.release_data import ReleaseData, available_releases
from adviewer.model import Graph
from adviewer.recorder import history_path_for, read_history

STATIC_DIR = Path(__file__).resolve().parent.parent / "ui" / "static"

# The UI loads only its own files and talks only to this server. Styles may be inline
# (React Flow sets them); scripts may not. No frame-ancestors: VS Code's Simple Browser
# shows the page in a frame (D17).
CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
       "connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'none'")
TOKEN_COOKIE = "adviewer_token"


def _guard(app: FastAPI, token: str | None, allowed_hosts: set[str] | None) -> None:
    """Security (owner, 2026-10-16): read-only API; on loopback only the expected Host
    headers are accepted (blocks DNS rebinding); with a token every request must carry it
    (header, cookie, or ?token= once — which sets an HttpOnly cookie); safe headers."""

    @app.middleware("http")
    async def guard(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        host = request.headers.get("host", "").rsplit(":", 1)[0].strip("[]")
        if allowed_hosts is not None and host not in allowed_hosts:
            return PlainTextResponse("host not allowed", status_code=400)
        from_query = False
        if token:
            offered = (request.headers.get("x-adviewer-token")
                       or request.headers.get("authorization", "").removeprefix("Bearer ").strip()
                       or request.cookies.get(TOKEN_COOKIE) or "")
            if not offered and request.query_params.get("token"):
                offered, from_query = request.query_params["token"], True
            if not secrets.compare_digest(offered.encode(), token.encode()):
                return PlainTextResponse("token required: open the URL printed by `adviewer serve`",
                                         status_code=401)
        if request.method not in ("GET", "HEAD"):
            return PlainTextResponse("read-only", status_code=405)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = CSP
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        if from_query:
            response.set_cookie(TOKEN_COOKIE, token or "", httponly=True, samesite="strict",
                                secure=request.url.scheme == "https")
        return response


class _State:
    def __init__(self, graph: Graph, live_path: Path | None, target_path: Path | None):
        self.static = graph
        self.graph = graph
        self.release: ReleaseData | None = next(
            (r for r in available_releases() if r.release == graph.meta.autoware_release), None
        )
        data = (lambda name: (self.release.load(name) or {}) if self.release else {})
        aliases = data("aliases.yaml")
        self.aliases: dict[str, list[str]] = aliases.get("aliases") or {}
        self.kits: dict[str, list[str]] = aliases.get("sensor_kits") or {}
        self.mrm: dict[str, Any] = data("mrm.yaml")
        self.subsystems: dict[str, str] = data("subsystems.yaml").get("subsystems") or {}
        self.live_path = live_path
        self.live_mtime = 0.0
        self.live_report: dict[str, Any] = {}
        self.target: dict[str, Any] = (
            yaml.safe_load(target_path.read_text(encoding="utf-8")) or {} if target_path else {}
        )
        self.doctor: tuple[float, dict[str, Any]] | None = None
        self.timeline: tuple[tuple[float, int, str, int], dict[str, Any]] | None = None

    def refresh_live(self) -> None:
        """Re-annotate a copy of the static graph when the probe dump changed on disk."""
        if self.live_path is None or not self.live_path.is_file():
            return
        mtime = self.live_path.stat().st_mtime
        if mtime == self.live_mtime:
            return
        try:
            dump = json.loads(self.live_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return  # half-written file: try again on the next poll
        graph = self.static.model_copy(deep=True)
        self.live_report = annotate(graph, dump)
        self.graph = graph
        self.live_mtime = mtime


def create_app(
    graph: Graph,
    live_path: Path | None = None,
    target_path: Path | None = None,
    token: str | None = None,
    allowed_hosts: set[str] | None = None,
) -> FastAPI:
    """The web app. `allowed_hosts` None = any Host header (use with a token on a network)."""
    app = FastAPI(title="adviewer", version="0.1.0", docs_url=None, redoc_url=None, openapi_url=None)
    _guard(app, token, allowed_hosts)
    st = _State(graph, live_path, target_path)
    app.state.graph = graph  # kept for callers that set it directly
    app.state.st = st

    def g() -> Graph:
        return st.graph

    def kit_members(name: str) -> list[str]:
        if name not in st.kits:
            raise HTTPException(404, f"unknown sensor kit {name}")
        return kit_nodes(g(), st.kits[name])

    @app.get("/api/meta")
    def meta() -> dict[str, Any]:
        gr = g()
        return {
            **gr.meta.model_dump(),
            "counts": {"nodes": len(gr.nodes), "topics": len(gr.topics), "edges": len(gr.edges),
                       "msg_types": len(gr.msg_types), "diag_units": len(gr.diag_units),
                       "params": len(gr.params), "warnings": len(gr.meta.warnings)},
            "release_data": st.release.release if st.release else "",
            "has_live": st.live_path is not None,
            "has_target": bool(st.target),
            "has_mrm": bool(st.mrm),
        }

    @app.get("/api/warnings")
    def warnings() -> list[str]:
        return g().meta.warnings

    @app.get("/api/search")
    def search_items(q: str = "", limit: int = 40) -> list[dict[str, Any]]:
        return search(g(), q, st.aliases, st.kits, limit)

    @app.get("/api/expand")
    def expand_item(id: str, dir: str = Query("back", pattern="^(back|fwd)$")) -> dict[str, Any]:
        if id.removeprefix("t:").removeprefix("n:") not in (g().topics if id.startswith("t:") else g().nodes):
            raise HTTPException(404, f"unknown element {id}")
        return expand(g(), id, dir)

    @app.get("/api/trace")
    def trace_item(id: str, dir: str = Query("back", pattern="^(back|fwd)$"), depth: int = 3) -> dict[str, Any]:
        return trace(g(), id, dir, max(1, min(depth, 20)))

    @app.get("/api/details")
    def details(id: str) -> dict[str, Any]:
        gr = g()
        kind, _, name = id.partition(":")
        result: dict[str, Any] | None = None
        if kind == "t":
            result = topic_details(gr, name)
            if result is not None:
                result["health"] = gr.topics[name].health
                result["live"] = gr.topics[name].live
                result["about"] = topic_about(gr, name, st.mrm or None)
        elif kind == "n":
            result = node_details(gr, name)
        elif kind == "p":
            result = {"kind": "param", "id": name, "entries": find_params(gr, name)}
            result["entries"] = [e for e in result["entries"] if e["name"] == name] or result["entries"]
        elif kind == "d" and name in gr.diag_units:
            result = {"kind": "diag", **gr.diag_units[name].model_dump()}
        elif kind == "kit":
            result = {"kind": "sensor_kit", "id": name, "packages": st.kits.get(name, []),
                      "nodes": kit_members(name)}
        elif kind == "s":
            result = next(({"kind": "switch", **s} for s in list_switches(gr) if s["name"] == name), None)
        if result is None:
            raise HTTPException(404, f"unknown item {id}")
        return result

    @app.get("/api/impact")
    def impact(id: str) -> dict[str, Any]:
        kind, _, name = id.partition(":")
        topics, nodes = [], []
        if kind == "t":
            topics = [name]
        elif kind == "n":
            nodes = [name]
        elif kind == "kit":
            nodes = kit_members(name)
        elif kind == "switch":
            sw = next((s for s in g().switches if s.name == name), None)
            if sw is None:
                raise HTTPException(404, f"unknown switch {name}")
            nodes = switch_nodes(sw)
        else:
            raise HTTPException(400, "impact is for a topic (t:), node (n:), sensor kit (kit:) or switch (switch:)")
        return shutdown_impact(g(), topics, nodes, st.mrm or None)

    @app.get("/api/switches")
    def switches() -> list[dict[str, Any]]:
        return list_switches(g())

    @app.get("/api/mrm")
    def mrm() -> dict[str, Any]:
        if not st.mrm:
            raise HTTPException(404, "no MRM data for this release (data/autoware-<release>/mrm.yaml)")
        return mrm_gates(g(), st.mrm)

    @app.get("/api/params")
    def params(q: str = "", limit: int = 100) -> list[dict[str, Any]]:
        return find_params(g(), q, limit)

    @app.get("/api/doctor")
    def doctor() -> dict[str, Any]:
        from adviewer.doctor import run_doctor

        if st.doctor is None or time.time() - st.doctor[0] > 60:
            st.doctor = (time.time(), run_doctor())
        return st.doctor[1]

    @app.get("/api/live")
    def live() -> dict[str, Any]:
        if st.live_path is None:
            return {"enabled": False}
        st.refresh_live()
        gr = g()
        return {
            "enabled": True,
            "available": bool(gr.meta.live_captured_at),
            "captured_at": gr.meta.live_captured_at,
            "health": {f"t:{t.id}": t.health for t in gr.topics.values() if t.health},
            "report": st.live_report,
        }

    @app.get("/api/timeline")
    def timeline_view(last: int = Query(360, ge=10, le=2880), points: int = Query(300, ge=0, le=5000)) -> dict[str, Any]:
        """Live dashboard (D31): events, change log with one-level answers, lanes, machine load."""
        if st.live_path is None:
            return {"enabled": False}
        history = history_path_for(st.live_path)
        if not history.is_file():
            return {"enabled": True, "available": False, "history": history.name}
        st.refresh_live()
        key = (history.stat().st_mtime, last, g().meta.live_captured_at, points)
        if st.timeline is None or st.timeline[0] != key:
            windows = read_history(history, last)
            st.timeline = (key, {"enabled": True, "available": bool(windows), **downsample(timeline(g(), windows), points)})
        return st.timeline[1]

    @app.get("/api/mapping")
    def mapping() -> dict[str, Any]:
        if not st.target:
            raise HTTPException(404, "start with: adviewer serve graph.json --target target.yaml")
        return mapping_report(g(), st.target, st.subsystems)

    @app.get("/api/mapping.md", response_class=PlainTextResponse)
    def mapping_md() -> str:
        return to_markdown(mapping(), g())

    # --- Phase-0 views, kept -------------------------------------------------------------
    @app.get("/api/graph")
    def full_graph(layer: str | None = Query(default=None)) -> dict[str, Any]:
        """All elements as {data: …} records (Phase-0 view). Optional layer filter (comma-separated)."""
        gr = g()
        layers = set(layer.split(",")) if layer else None
        ext = set(gr.external_inputs())
        nodes = [
            {"data": {"id": f"n:{n.id}", "label": n.id, "kind": "node", "layer": n.layer.value, "package": n.package}}
            for n in gr.nodes.values() if layers is None or n.layer.value in layers
        ]
        topics = [
            {"data": {"id": f"t:{t.id}", "label": t.id, "kind": "topic", "layer": t.layer.value,
                      "msg_type": t.msg_type, "external": t.id in ext}}
            for t in gr.topics.values() if layers is None or t.layer.value in layers
        ]
        ids = {e["data"]["id"] for e in nodes + topics}
        edges = []
        for e in gr.edges:
            s, t = (f"n:{e.node}", f"t:{e.topic}") if e.direction.value == "publish" else (f"t:{e.topic}", f"n:{e.node}")
            if s in ids and t in ids:
                edges.append({"data": {"id": f"{s}->{t}", "source": s, "target": t,
                                       "confidence": e.confidence.value, "evidence": e.evidence}})
        return {"elements": nodes + topics + edges}

    @app.get("/api/topic")
    def topic(name: str) -> dict[str, Any]:
        result = topic_details(g(), name)
        if result is None:
            raise HTTPException(404, f"unknown topic {name}")
        return result

    @app.get("/api/node")
    def node(name: str) -> dict[str, Any]:
        result = node_details(g(), name)
        if result is None:
            raise HTTPException(404, f"unknown node {name}")
        return result

    @app.get("/api/external")
    def external() -> dict[str, Any]:
        return {"inputs": g().external_inputs(), "outputs": g().external_outputs()}

    @app.get("/api/drilldown")
    def drill(topic: str, depth: int = 2) -> dict[str, Any]:
        return drilldown(g(), topic, depth)

    @app.get("/api/criticality")
    def crit(topic: str) -> dict[str, Any]:
        return criticality(g(), topic)

    @app.get("/api/diag")
    def diag() -> dict[str, Any]:
        return {k: v.model_dump() for k, v in g().diag_units.items()}

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app
