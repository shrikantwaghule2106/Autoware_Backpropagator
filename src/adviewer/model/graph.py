"""The single data contract of adviewer.

Every ingest source produces or annotates a `Graph`; every analysis and the API
consume it. Keep this module free of parsing and IO (besides JSON (de)serialisation).
"""
from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, PrivateAttr


class Layer(str, Enum):
    sensing = "sensing"
    localization = "localization"
    perception = "perception"
    planning = "planning"
    control = "control"
    vehicle = "vehicle"
    system = "system"
    map = "map"
    common = "common"
    api = "api"  # autoware_core/api: AD API adaptors (/api/…)
    evaluator = "evaluator"
    simulator = "simulator"
    visualization = "visualization"
    e2e = "e2e"  # autoware_universe/e2e: end-to-end planners
    launch = "launch"
    external = "external"  # provided from outside Autoware (sim / hardware)
    unknown = "unknown"


class Confidence(str, Enum):
    """How the fact was established. Higher is more trustworthy."""

    static_convention = "static-convention"  # e.g. ~/input vs ~/output naming heuristic
    static_source = "static-source"  # create_publisher / create_subscription found in code
    live = "live"  # observed by the ROS 2 probe
    user = "user"  # asserted by the user (mapping file / UI override)


class Direction(str, Enum):
    publish = "publish"  # node -> topic
    subscribe = "subscribe"  # topic -> node


class Doc(BaseModel):
    """Text taken from Autoware itself, never written by adviewer: a package README's
    Purpose section or Input/Output table row, or a .msg file's header comment."""

    text: str
    source: str  # file:line
    package: str = ""
    role: str = ""  # "purpose", "input" (the package subscribes), "output" (it publishes)


class Rate(BaseModel):
    """An expected publish rate / timer period and where it comes from (FR-6)."""

    hz: float | None = None  # None when the period is not a literal
    kind: str  # "timer" (C++ timer), "param" (rate parameter), "monitor" (topic_state_monitor
    #            thresholds), "live" (measured by the probe)
    evidence: str  # file:line or probe record
    note: str = ""  # e.g. "warn below 5 Hz, error below 1 Hz" or the parameter name


class Node(BaseModel):
    id: str  # fully-qualified node name, e.g. /system/mrm_handler
    package: str = ""
    executable: str = ""
    namespace: str = "/"
    layer: Layer = Layer.unknown
    source_file: str = ""  # launch file (relative to autoware root) that declares it
    params: dict[str, str] = Field(default_factory=dict)
    rates: list[Rate] = Field(default_factory=list)  # timers and rate parameters (FR-6)
    docs: list[Doc] = Field(default_factory=list)  # the package README's purpose


class Topic(BaseModel):
    id: str  # fully-resolved topic name
    msg_type: str = ""  # e.g. tier4_system_msgs/msg/OperationModeAvailability
    qos: dict[str, str] = Field(default_factory=dict)  # reliability, durability, depth …
    rate_hz: float | None = None  # measured live (Phase 4)
    layer: Layer = Layer.unknown
    rates: list[Rate] = Field(default_factory=list)  # expected rates with their source (FR-6)
    docs: list[Doc] = Field(default_factory=list)  # README Input/Output rows that describe it
    # Online mode (Phase 4): last probe window (count, rate_hz, jitter_ms, max_gap_ms,
    # bandwidth_Bps, mean_size_B, types) and the health derived from it (FR-9):
    # healthy | degraded | unhealthy | idle | "" (not observed)
    live: dict[str, Any] = Field(default_factory=dict)
    health: str = ""


class Edge(BaseModel):
    node: str
    topic: str
    direction: Direction
    confidence: Confidence
    evidence: str  # "path/to/file.launch.xml:42" or "probe:2026-09-06T…"
    # this endpoint's QoS (F7b): reliability, durability, history, depth, as_written
    qos: dict[str, str] = Field(default_factory=dict)

    @property
    def key(self) -> tuple[str, str, Direction]:
        return (self.node, self.topic, self.direction)


class MsgField(BaseModel):
    name: str
    type: str  # e.g. float64, geometry_msgs/Point
    is_array: bool = False
    array_len: int | None = None


class MsgType(BaseModel):
    id: str  # pkg/msg/Name
    fields: list[MsgField] = Field(default_factory=list)
    constants: dict[str, str] = Field(default_factory=dict)
    source_file: str = ""
    description: str = ""  # the .msg file's header comment, as written


class DiagUnit(BaseModel):
    """One unit in the diagnostic_graph_aggregator YAML."""

    path: str  # unique unit path, e.g. /autoware/modes/autonomous
    type: str  # and | or | diag | const | link | remap | …
    children: list[str] = Field(default_factory=list)  # paths of child units
    diag_name: str = ""  # for type == diag: the /diagnostics status name it watches
    source_file: str = ""
    extra: dict[str, str] = Field(default_factory=dict)


class ParamKind(str, Enum):
    param_file = "param-file"  # a leaf of a ROS 2 parameter YAML loaded with <param from=…>
    launch_param = "launch-param"  # <param name=… value=…> or parameters=[{…}] in a launch file
    launch_arg = "launch-arg"  # <arg name=… default=…> / DeclareLaunchArgument: overridable


class Param(BaseModel):
    """One place where a parameter value is set (FR-7). A name may have several entries."""

    node: str = ""  # node the value applies to; "" for a launch argument
    name: str  # e.g. max_vel (nested YAML keys joined by '.'), or the launch argument name
    value: str  # as written; lists as JSON text, e.g. ["/a", "/b"]
    kind: ParamKind
    evidence: str  # file:line
    via_arg: str = ""  # launch argument, settable on the command line, that controls it
    via_expr: str = ""  # how via_arg reaches it, e.g. "a = $(var b)/x.yaml [file:43]"
    package: str = ""  # package that installs the file named in `evidence`


class Gate(BaseModel):
    """One launch element a switch turns on or off."""

    evidence: str  # file:line of the gated element
    tag: str  # include | group | node | …  (or the Python call, e.g. Node)
    condition: str  # the if= / unless= text as written
    on: bool | None  # runs with the current values? None = undecidable
    target: str = ""  # what it starts: the included file, or the node names inside
    nodes: list[str] = Field(default_factory=list)  # nodes started under it (when on)


class Switch(BaseModel):
    """A launch variable used in if= / unless= conditions: how Autoware is made lighter
    or reconfigured. `cli_arg` is the argument a user can set on the command line to
    change it (empty when the value is fixed in a launch file)."""

    name: str
    value: str
    cli_arg: str = ""
    cli_chain: str = ""
    declared: str = ""  # file:line where its current value comes from
    gates: list[Gate] = Field(default_factory=list)


class GraphMeta(BaseModel):
    autoware_root: str = ""
    autoware_commit: str = ""
    autoware_release: str = ""  # detected release whose data files were used, e.g. 1.9.0
    layout: str = ""  # "install" (share/<pkg>/ dirs, e.g. from the Docker image) or "source"
    entry_launch: str = ""  # top-level launch file walked, relative to autoware_root
    entry_package: str = ""  # package of entry_launch (for `ros2 launch <pkg> <file>`)
    live_captured_at: str = ""  # probe dump merged by `adviewer annotate` (Phase 4)
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    sources: list[str] = Field(default_factory=list)  # ingest modules that contributed
    warnings: list[str] = Field(default_factory=list)


class Graph(BaseModel):
    meta: GraphMeta = Field(default_factory=GraphMeta)
    nodes: dict[str, Node] = Field(default_factory=dict)
    topics: dict[str, Topic] = Field(default_factory=dict)
    edges: list[Edge] = Field(default_factory=list)
    msg_types: dict[str, MsgType] = Field(default_factory=dict)
    diag_units: dict[str, DiagUnit] = Field(default_factory=dict)
    params: list[Param] = Field(default_factory=list)
    switches: list[Switch] = Field(default_factory=list)
    # edge key -> index in `edges`; not serialised, rebuilt on demand
    _edge_index: dict[tuple[str, str, Direction], int] = PrivateAttr(default_factory=dict)

    # --- mutation helpers -------------------------------------------------
    def add_node(self, node: Node) -> Node:
        existing = self.nodes.get(node.id)
        if existing is None:
            self.nodes[node.id] = node
            return node
        # merge: keep first non-empty values
        for f in ("package", "executable", "source_file"):
            if not getattr(existing, f):
                setattr(existing, f, getattr(node, f))
        if existing.layer == Layer.unknown:
            existing.layer = node.layer
        existing.params.update(node.params)
        return existing

    def add_topic(self, topic: Topic) -> Topic:
        existing = self.topics.get(topic.id)
        if existing is None:
            self.topics[topic.id] = topic
            return topic
        if not existing.msg_type:
            existing.msg_type = topic.msg_type
        if existing.layer == Layer.unknown:
            existing.layer = topic.layer
        if existing.rate_hz is None:
            existing.rate_hz = topic.rate_hz
        existing.qos.update(topic.qos)
        return existing

    def add_edge(self, edge: Edge) -> Edge:
        """Add or upgrade an edge; higher confidence replaces lower."""
        order = list(Confidence)
        if len(self._edge_index) != len(self.edges):  # loaded from JSON or edited directly
            self._edge_index = {e.key: i for i, e in enumerate(self.edges)}
        i = self._edge_index.get(edge.key)
        if i is not None:
            e = self.edges[i]
            if order.index(edge.confidence) > order.index(e.confidence):
                self.edges[i] = edge
                return edge
            return e
        self._edge_index[edge.key] = len(self.edges)
        self.edges.append(edge)
        self.topics.setdefault(edge.topic, Topic(id=edge.topic))
        self.nodes.setdefault(edge.node, Node(id=edge.node))
        return edge

    def warn(self, msg: str) -> None:
        self.meta.warnings.append(msg)

    # --- queries ------------------------------------------------------------
    def publishers(self, topic: str) -> Iterator[Edge]:
        return (e for e in self.edges if e.topic == topic and e.direction == Direction.publish)

    def subscribers(self, topic: str) -> Iterator[Edge]:
        return (e for e in self.edges if e.topic == topic and e.direction == Direction.subscribe)

    def external_inputs(self) -> list[str]:
        """Topics that are subscribed inside Autoware but have no Autoware publisher.

        These are exactly what a sim/hardware bridge must provide.
        """
        subscribed = {e.topic for e in self.edges if e.direction == Direction.subscribe}
        published = {e.topic for e in self.edges if e.direction == Direction.publish}
        return sorted(subscribed - published)

    def external_outputs(self) -> list[str]:
        """Topics published inside Autoware with no Autoware subscriber (candidates a bridge consumes)."""
        subscribed = {e.topic for e in self.edges if e.direction == Direction.subscribe}
        published = {e.topic for e in self.edges if e.direction == Direction.publish}
        return sorted(published - subscribed)

    # --- IO -------------------------------------------------------------------
    def save(self, path: str | Path) -> None:
        Path(path).write_text(self.model_dump_json(indent=1), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> Graph:
        return cls.model_validate_json(Path(path).read_text(encoding="utf-8"))
