"""Parse ROS 2 XML and YAML launch files into nodes, topics and edges.

Supported: <arg>, <let>, <group>, <push-ros-namespace>, <node>, <remap>, <param>,
<include> (resolved through $(find-pkg-share)), $(var …), $(find-pkg-share …).
Nested substitutions are resolved innermost first, e.g. $(find-pkg-share $(var x)).
YAML launch files (top-level `launch:`) are converted to the same element tree (F3).
An <include> is unscoped, as in ROS 2 Jazzy: args it sets and args the included file
declares stay visible to the includer; only a scoped <group> isolates them.
Not supported yet (recorded as warnings): <composable_node> containers beyond simple
node extraction, $(env …), $(eval …).

Direction heuristic (D8): a remap whose `from` contains
'input' → subscribe, 'output' → publish. We do NOT invent edges: unknown direction
is recorded as a warning and the topic is still added to the graph (edge-less).
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any
from xml.parsers import expat

import yaml

from adviewer.model import (
    Confidence,
    Direction,
    Edge,
    Gate,
    Graph,
    Layer,
    Node,
    Param,
    ParamKind,
    Switch,
    Topic,
)

from .conditions import eval_expression, evaluate_condition
from .cpp_source import Endpoint
from .package_xml import PackageInfo
from .param_yaml import ParamLeaf, applies_to, read_param_file
from .qos import interpret_qos


class LaunchContext:
    def __init__(self, packages: dict[str, PackageInfo], root: Path, overlays: list[Path] | None = None):
        self.packages = packages
        self.root = root
        self.overlays = overlays or []  # later roots; files there are named "<dir>/…"
        self.visited: set[Path] = set()  # every launch file parsed at least once
        self.stack: list[Path] = []  # include chain being parsed, guards against cycles
        self.endpoints: dict[str, list[tuple[str, Endpoint]]] = {}
        self._by_dir: dict[Path, PackageInfo] | None = None
        self.emulators: list[str] = []  # launch emulators enabled for the release
        self.switches: dict[str, Switch] = {}  # launch variables used in conditions (switches)
        self.gate_stack: list[Gate] = []  # gates of the taken elements being walked
        self._param_files: dict[Path, list[ParamLeaf] | str] = {}  # leaves, or the error text

    def param_file(self, path: Path) -> list[ParamLeaf] | str:
        """Leaves of a parameter YAML (read once), or the error message."""
        if path not in self._param_files:
            try:
                self._param_files[path] = read_param_file(path)
            except (yaml.YAMLError, OSError, UnicodeDecodeError) as exc:
                self._param_files[path] = str(exc).splitlines()[0]
        return self._param_files[path]

    def package_of(self, path: Path) -> PackageInfo | None:
        """The package whose directory contains `path` (nearest one), or None."""
        if self._by_dir is None:
            self._by_dir = {info.path.resolve(): info for info in self.packages.values()}
        for d in Path(path).resolve().parents:
            if d in self._by_dir:
                return self._by_dir[d]
        return None

    def package_name(self, rel: str) -> str:
        """Name of the package owning a file given as ctx.rel() returned it ('' if none)."""
        for overlay in self.overlays:
            if rel.startswith(overlay.name + "/"):
                info = self.package_of(overlay.parent / rel)
                if info:
                    return info.name
        info = self.package_of(self.root / rel)
        return info.name if info else ""

    def rel(self, path: Path) -> str:
        """path relative to the autoware root (overlay files: '<overlay dir>/…'), for evidence;
        absolute if outside all roots."""
        path = Path(path)
        for overlay in reversed(self.overlays):
            if path.is_relative_to(overlay):
                return f"{overlay.name}/{path.relative_to(overlay).as_posix()}"
        try:
            return path.relative_to(self.root).as_posix()  # "/" on every OS
        except ValueError:
            return str(path)

    def find_pkg_share(self, pkg: str) -> Path | None:
        info = self.packages.get(pkg)
        return info.path if info else None

    def endpoint(self, pkg: str, name: str) -> tuple[str, Endpoint] | None:
        """(file, endpoint) if pkg's C++ declares `name` with one unambiguous kind; else None."""
        matches = [(f, e) for f, e in self.endpoints.get(pkg, []) if e.name == name]
        kinds = {e.kind for _, e in matches}
        return matches[0] if len(kinds) == 1 else None



def _unquote(text: str) -> str:
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "'\"":
        return text[1:-1]
    return text


def _unescape(text: str) -> str:
    return re.sub(r"\\(.)", r"\1", text)


def _closing(text: str, start: int) -> int:
    """Index of the ')' closing the '$(' at `start`, or -1 if unbalanced."""
    depth = 0
    i = start + 1  # at the '('
    while i < len(text):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def substitute(value: str, vars_: dict[str, str], ctx: LaunchContext, graph: Graph) -> str:
    """Resolve $(var x), $(find-pkg-share pkg), $(eval expr), $(env X default).

    Nested substitutions in the source text are resolved innermost first
    ($(find-pkg-share $(var x))); a result is inserted as plain text and never expanded
    again, as in ROS — a value such as x = "$(var x)/a" cannot grow without end.
    Unknown or undecidable substitutions are left verbatim. `$(env X default)` takes the
    default: the target's environment is not known offline (stock image = unset).
    Literal text drops backslash escapes (`\\'` → `'`), as Jazzy's
    launch/frontend/parse_substitution.py `replace_escaped_characters` does.
    """

    def resolve(inner: str, original: str) -> str:
        if "$(" in inner:  # an inner substitution stayed unresolved
            return original
        kind, _, arg = inner.partition(" ")
        arg = arg.strip()
        if kind == "var":
            return vars_.get(arg, original)
        if kind == "find-pkg-share":
            p = ctx.find_pkg_share(arg)
            if p is None:
                graph.warn(f"find-pkg-share: unknown package '{arg}'")
                return original
            return str(p)
        if kind == "eval":
            result = eval_expression(_unquote(arg))  # $(eval "expr"): quotes are syntax
            return original if result is None else result
        if kind == "env":
            _, _, default = arg.partition(" ")
            return _unquote(default.strip()) if default else original
        return original  # $(dirname), $(command …), $(anon …) … untouched

    out: list[str] = []
    i = 0
    while True:
        start = value.find("$(", i)
        end = _closing(value, start) if start >= 0 else -1
        if end < 0:
            out.append(_unescape(value[i:]))
            return "".join(out)
        out.append(_unescape(value[i:start]))
        inner = substitute(value[start + 2 : end], vars_, ctx, graph)
        out.append(resolve(inner, value[start : end + 1]))
        i = end + 1


def _join_ns(ns: str, name: str) -> str:
    if name.startswith("/"):
        return name
    ns = ns.rstrip("/")
    return f"{ns}/{name}" if ns else f"/{name}"


def _resolve_topic(name: str, node_fqn: str, ns: str) -> str:
    """ROS 2 name resolution for '~/x', 'x', '/x'."""
    if name.startswith("~/"):
        return f"{node_fqn}/{name[2:]}"
    if name == "~":
        return node_fqn
    return _join_ns(ns, name)


def _direction_from_convention(from_name: str) -> Direction | None:
    low = from_name.lower()
    if "input" in low:
        return Direction.subscribe
    if "output" in low:
        return Direction.publish
    return None

def _add_remap(graph: Graph, ctx: LaunchContext, node: Node, frm: str, to: str, evidence: str) -> None:
    """Topic and edge for one remap of `node`; no edge if the direction is unknown."""
    topic = graph.add_topic(Topic(id=_resolve_topic(to, node.id, node.namespace), layer=node.layer))
    found = ctx.endpoint(node.package, frm)
    if found is not None:
        cpp_file, ep = found
        if ep.msg_type and not topic.msg_type:
            topic.msg_type = ep.msg_type
        qos = interpret_qos(ep.qos)
        if ep.kind == "publisher" and not topic.qos:
            topic.qos = dict(qos)  # FR-5 shows the (first) publisher's profile per topic
        graph.add_edge(
            Edge(
                node=node.id,
                topic=topic.id,
                direction=Direction.subscribe if ep.kind == "subscription" else Direction.publish,
                confidence=Confidence.static_source,
                evidence=f"{evidence}; {cpp_file}:{ep.line}: create_{ep.kind}",
                qos=qos,
            )
        )
        return
    direction = _direction_from_convention(frm)
    if direction is None:
        graph.warn(f"{evidence} on {node.id}: direction unknown (no edge emitted)")
        return
    graph.add_edge(
        Edge(node=node.id, topic=topic.id, direction=direction,
             confidence=Confidence.static_convention, evidence=evidence)
    )

def parse_launch_file(
    path: Path,
    graph: Graph,
    ctx: LaunchContext,
    vars_: dict[str, str] | None = None,
    namespace: str = "/",
    layer: Layer = Layer.unknown,
) -> None:
    """Parse one launch file. `vars_` is shared with the caller (unscoped include).

    A file may be included several times (e.g. once per lidar), so it is parsed each time;
    only an include cycle is refused.
    """
    path = Path(path)
    if path in ctx.stack:
        graph.warn(f"include cycle: {path} includes itself, skipped")
        return
    ctx.visited.add(path)
    rel = ctx.rel(path)
    vars_ = vars_ if vars_ is not None else {}
    own = _layer_of(path, ctx)  # the file's own package decides; the includer's is a fallback (F8)
    layer = own if own != Layer.unknown else layer
    if path.suffix == ".py":
        from .launch_py import parse_launch_py

        parse_launch_py(path, graph, ctx, namespace, layer, vars_)
        return
    if path.suffix in (".yaml", ".yml"):
        root = _yaml_launch_root(path, graph, rel)
    else:
        try:
            root = _read_xml(path)
        except (expat.ExpatError, OSError) as exc:
            graph.warn(f"launch parse error {rel}: {exc}")
            return
    if root is None:
        return
    ctx.stack.append(path)
    try:
        _walk(root, graph, ctx, vars_, namespace, rel, layer)
    finally:
        ctx.stack.pop()


def _read_xml(path: Path) -> ET.Element:
    """Parse XML keeping each element's line in the attribute `__line__` (for evidence)."""
    parser = expat.ParserCreate()
    stack: list[ET.Element] = []
    roots: list[ET.Element] = []

    def start(tag: str, attrs: dict[str, str]) -> None:
        el = ET.Element(tag, attrs)
        el.set("__line__", str(parser.CurrentLineNumber))
        (stack[-1].append(el) if stack else roots.append(el))
        stack.append(el)

    def end(_tag: str) -> None:
        stack.pop()

    parser.StartElementHandler = start
    parser.EndElementHandler = end
    parser.Parse(Path(path).read_bytes(), True)
    return roots[0]


def _where(rel: str, el: ET.Element) -> str:
    """Evidence 'file:line' (YAML launch elements have no line: 'file')."""
    line = el.get("__line__")
    return f"{rel}:{line}" if line else rel


_VAR = re.compile(r"\$\(var ([^()\s]+)\)")
_ORIGIN = "\0origin:"  # vars_ key prefix for where a launch variable got its value


def _set_var(
    vars_: dict[str, str], name: str, raw: str, how: str, where: str, ctx: LaunchContext, graph: Graph
) -> None:
    """Set a launch variable and remember its origin: how ("default" = a declared <arg>
    default, overridable on the command line; "include" = passed by an includer;
    "let" = fixed), the raw text and file:line. Copies of vars_ carry the origins along."""
    if raw.strip() == f"$(var {name})" and name in vars_:
        return  # <arg name="x" value="$(var x)"/> passes x on unchanged: keep where it came from
    vars_[name] = substitute(raw, vars_, ctx, graph)
    vars_[_ORIGIN + name] = f"{how}\t{raw}\t{where}"


def _via_arg(vars_: dict[str, str], raw: str | None) -> tuple[str, str]:
    """(launch argument settable on the command line that controls `raw`, how it gets there).

    Follows include/let origins to the declared argument whose default is still in effect;
    e.g. common_param_path is passed by tier4_planning_component.launch.xml as
    "$(var common_config_path)/common.param.yaml", so the handle is common_config_path.
    ('', '') if the value is fixed in a file or built from more than one variable.
    """
    text = raw or ""
    chain: list[str] = []
    for _ in range(20):
        names = _VAR.findall(text)
        if len(names) != 1 or names[0] not in vars_:
            return "", ""
        name = names[0]
        origin = vars_.get(_ORIGIN + name, "")
        how, _, rest = origin.partition("\t")
        src, _, where = rest.partition("\t")
        if how in ("", "default"):  # declared default (or set from outside): settable
            return name, " ← ".join(chain)
        chain.append(f"{name} = {src} [{where}]")
        text = src
    return "", ""


def add_param_file(
    graph: Graph,
    ctx: LaunchContext,
    node: Node,
    path_text: str,
    where: str,
    via_arg: str = "",
    via_expr: str = "",
) -> None:
    """Index the leaves of a parameter file that apply to `node` (FR-7)."""
    if "$(" in path_text:
        graph.warn(f"{where}: parameter file not resolved '{path_text}'")
        return
    path = Path(path_text)
    if not path.is_file():
        graph.warn(f"{where}: parameter file not found '{path_text}'")
        return
    leaves = ctx.param_file(path)
    if isinstance(leaves, str):
        graph.warn(f"{where}: parameter file unreadable '{ctx.rel(path)}': {leaves}")
        return
    frel = ctx.rel(path)
    owner = ctx.package_of(path)
    for leaf in leaves:
        if applies_to(leaf.node_key, node.id):
            node.params[leaf.name] = leaf.value
            graph.params.append(
                Param(node=node.id, name=leaf.name, value=leaf.value, kind=ParamKind.param_file,
                      evidence=f"{frel}:{leaf.line}", via_arg=via_arg, via_expr=via_expr,
                      package=owner.name if owner else "")
            )


def _yaml_launch_root(path: Path, graph: Graph, rel: str) -> ET.Element | None:
    """A YAML launch file as the equivalent XML element tree, or None after a warning.

    Mirrors launch_yaml/entity.py (ROS 2 Jazzy): every entry is a one-key mapping
    {tag: attributes}; `children:` and attributes holding a list of mappings (remap,
    param, arg) become child elements.
    """
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (yaml.YAMLError, OSError) as exc:
        graph.warn(f"launch parse error {rel}: {exc}")
        return None
    if not isinstance(data, dict) or not isinstance(data.get("launch"), list):
        graph.warn(f"launch parse error {rel}: YAML launch file needs a top-level 'launch:' list")
        return None
    root = ET.Element("launch")
    _yaml_children(root, data["launch"], graph, rel)
    return root


def _yaml_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return "" if value is None else str(value)


def _yaml_entity(tag: str, value: Any, graph: Graph, rel: str) -> ET.Element:
    el = ET.Element(tag)
    if isinstance(value, list):
        _yaml_children(el, value, graph, rel)
    elif isinstance(value, dict):
        for key, v in value.items():
            if key == "children" and isinstance(v, list):
                _yaml_children(el, v, graph, rel)
            elif isinstance(v, list) and v and all(isinstance(x, dict) for x in v):
                for item in v:
                    el.append(_yaml_entity(str(key), item, graph, rel))
            else:
                el.set(str(key), _yaml_scalar(v))
    return el


def _yaml_children(parent: ET.Element, entries: list[Any], graph: Graph, rel: str) -> None:
    for entry in entries:
        if not isinstance(entry, dict) or len(entry) != 1:
            graph.warn(f"{rel}: YAML launch entry should be a one-key mapping, got {entry!r:.60}")
            continue
        ((tag, value),) = entry.items()
        parent.append(_yaml_entity(str(tag), value, graph, rel))


def _walk(
    el: ET.Element,
    graph: Graph,
    ctx: LaunchContext,
    vars_: dict[str, str],
    ns: str,
    rel: str,
    layer: Layer,
) -> None:
    for child in list(el):
        gate = _gate(child, ctx, vars_, rel)
        holds, decided = _condition(child, graph, ctx, vars_, rel)
        if gate is not None:
            gate.on = holds if decided else None
        if not holds:
            continue
        if gate is not None:
            ctx.gate_stack.append(gate)
        try:
            _walk_child(child, graph, ctx, vars_, ns, rel, layer)
            if child.tag in ("push-ros-namespace", "push_ros_namespace"):
                ns = _join_ns(ns, substitute(child.get("namespace", ""), vars_, ctx, graph))
        finally:
            if gate is not None:
                ctx.gate_stack.pop()


_GATED_TAGS = {"include", "group", "node", "node_container", "composable_node", "load_composable_node",
               "executable"}


def _gate(el: ET.Element, ctx: LaunchContext, vars_: dict[str, str], rel: str) -> Gate | None:
    """Record a conditional element under every launch variable its condition reads."""
    cond = el.get("if") or el.get("unless")
    if cond is None or el.tag not in _GATED_TAGS:
        return None
    names = list(dict.fromkeys(_VAR.findall(cond)))
    if not names:
        return None
    if el.tag == "include":
        target = ctx.rel(Path(substitute(el.get("file", ""), vars_, ctx, Graph())))
    elif el.tag in ("node", "node_container", "composable_node", "executable"):
        target = el.get("name") or el.get("exec") or el.get("pkg") or ""
    else:
        inner = [c.get("name") or c.get("exec") or "" for c in el.iter() if c.tag in ("node", "composable_node")]
        files = [Path(c.get("file", "")).name for c in el.iter() if c.tag == "include"]
        target = ", ".join([n for n in inner if n][:6] + [f for f in files if f][:6])
    gate = Gate(evidence=_where(rel, el), tag=el.tag, condition=(("unless: " if el.get("unless") else "") + cond),
                on=None, target=target)
    return register_gate(ctx, vars_, names, gate)


def register_gate(ctx: LaunchContext, vars_: dict[str, str], names: list[str], gate: Gate) -> Gate:
    """File `gate` under each launch variable in `names` (switches); one record per element
    even when its file is included several times."""
    for name in names:
        sw = ctx.switches.get(name)
        if sw is None:
            via, chain = _via_arg(vars_, f"$(var {name})")
            origin = vars_.get(_ORIGIN + name, "")
            sw = ctx.switches[name] = Switch(name=name, value=vars_.get(name, ""), cli_arg=via,
                                             cli_chain=chain, declared=origin.split("\t")[-1] if origin else "")
        known = next((g for g in sw.gates if g.evidence == gate.evidence), None)
        if known is None:
            sw.gates.append(gate)
        else:
            gate = known
    return gate

def _walk_child(
    child: ET.Element,
    graph: Graph,
    ctx: LaunchContext,
    vars_: dict[str, str],
    ns: str,
    rel: str,
    layer: Layer,
) -> None:
    tag = child.tag
    if tag == "let":
        _set_var(vars_, child.get("name", ""), child.get("value", ""), "let", _where(rel, child), ctx, graph)
    elif tag == "arg":
        name = child.get("name", "")
        default = child.get("default")
        origin = vars_.get(_ORIGIN + name, "")
        overridden = name in vars_ and not origin.startswith("default")
        if default is not None and not overridden:  # a default in effect = overridable (FR-7)
            graph.params.append(
                Param(name=name, value=substitute(default, vars_, ctx, graph),
                      kind=ParamKind.launch_arg, evidence=_where(rel, child),
                      package=ctx.package_name(rel))
            )
        if name not in vars_:  # a value passed by <include><arg> wins over the default
            if child.get("value") is not None:  # fixed value, not overridable
                _set_var(vars_, name, child.get("value", ""), "let", _where(rel, child), ctx, graph)
            elif default is not None:
                _set_var(vars_, name, default, "default", _where(rel, child), ctx, graph)
    elif tag == "group":
        scoped = dict(vars_) if child.get("scoped", "true") != "false" else vars_
        _walk(child, graph, ctx, scoped, ns, rel, layer)
    elif tag == "push-ros-namespace" or tag == "push_ros_namespace":
        pass  # applied by _walk to the following siblings
    elif tag == "include":
        _include(child, graph, ctx, vars_, ns, rel, layer)
    elif tag in ("node", "node_container", "composable_node"):
        _node(child, graph, ctx, vars_, ns, rel, layer)
    elif tag == "load_composable_node":
        _walk(child, graph, ctx, vars_, ns, rel, layer)
    else:
        # unknown tags (set_parameter, executable, …): descend anyway
        _walk(child, graph, ctx, vars_, ns, rel, layer)


def _condition(
    el: ET.Element, graph: Graph, ctx: LaunchContext, vars_: dict[str, str], rel: str
) -> tuple[bool, bool]:
    """(runs, decided) for the element's if=/unless= (F10).

    An undecidable condition counts as running (the element is kept, as before F10) and is
    reported, so nothing disappears silently.
    """
    decided = True
    for attr, wanted in (("if", True), ("unless", False)):
        text = el.get(attr)
        if text is None:
            continue
        value = evaluate_condition(substitute(text, vars_, ctx, graph))
        if value is None:
            decided = False
            graph.warn(f'{rel}: <{el.tag}> {attr}="{text}" not resolved, element kept')
        elif value != wanted:
            return False, True
    return True, decided


def _condition_holds(
    el: ET.Element, graph: Graph, ctx: LaunchContext, vars_: dict[str, str], rel: str
) -> bool:
    """False if the element's if=/unless= says it does not run (F10)."""
    return _condition(el, graph, ctx, vars_, rel)[0]


def _include(
    el: ET.Element, graph: Graph, ctx: LaunchContext, vars_: dict[str, str], ns: str, rel: str, layer: Layer
) -> None:
    file_attr = substitute(el.get("file", ""), vars_, ctx, graph)
    # Unscoped, as IncludeLaunchDescription in ROS 2 Jazzy (launch 3.4): the args are set in
    # the includer's own context, one after another, then the file runs in that context.
    for arg in el.findall("arg"):
        if _condition_holds(arg, graph, ctx, vars_, rel):
            _set_var(vars_, arg.get("name", ""), arg.get("value", ""), "include", _where(rel, arg), ctx, graph)
    if "$(" in file_attr:
        graph.warn(f"{rel}: unresolved include '{file_attr}'")
        return
    target = Path(file_attr)
    if not target.exists():
        graph.warn(f"{rel}: include not found '{file_attr}'")
        return
    parse_launch_file(target, graph, ctx, vars_, ns, layer)


def _layer_of(path: Path, ctx: LaunchContext) -> Layer:
    info = ctx.package_of(path)
    return info.layer if info else Layer.unknown


def _node(
    el: ET.Element, graph: Graph, ctx: LaunchContext, vars_: dict[str, str], ns: str, rel: str, layer: Layer
) -> None:
    sub = lambda s: substitute(s or "", vars_, ctx, graph)
    pkg = sub(el.get("pkg"))
    exe = sub(el.get("exec", el.get("plugin", "")))
    name = sub(el.get("name")) or exe or pkg
    node_ns = _join_ns(ns, sub(el.get("namespace", ""))) if el.get("namespace") else ns
    fqn = _join_ns(node_ns, name)
    pkg_layer = ctx.packages[pkg].layer if pkg in ctx.packages else Layer.unknown
    node = graph.add_node(
        Node(
            id=fqn,
            package=pkg,
            executable=exe,
            namespace=node_ns,
            layer=pkg_layer if pkg_layer != Layer.unknown else layer,
            source_file=rel,
        )
    )
    for gate in ctx.gate_stack:
        if fqn not in gate.nodes:
            gate.nodes.append(fqn)
    for param in el.findall("param"):
        if not _condition_holds(param, graph, ctx, vars_, rel):
            continue
        where = _where(rel, param)
        pname, pval = sub(param.get("name", "")), sub(param.get("value", ""))
        if pname:
            node.params[pname] = pval
            via, via_expr = _via_arg(vars_, param.get("value"))
            graph.params.append(
                Param(node=fqn, name=pname, value=pval, kind=ParamKind.launch_param,
                      evidence=where, via_arg=via, via_expr=via_expr,
                      package=ctx.package_name(rel))
            )
        elif param.get("from"):
            via, via_expr = _via_arg(vars_, param.get("from"))
            add_param_file(graph, ctx, node, sub(param.get("from")), where, via, via_expr)
    for remap in el.findall("remap"):
        if not _condition_holds(remap, graph, ctx, vars_, rel):
            continue
        frm, to = sub(remap.get("from", "")), sub(remap.get("to", ""))
        if not frm or not to:
            continue
        _add_remap(graph, ctx, node, frm, to, f"{_where(rel, remap)}: remap from='{frm}' to='{to}'")

    # nested composable nodes inside a container
    for child in el:
        if child.tag == "composable_node" and _condition_holds(child, graph, ctx, vars_, rel):
            _node(child, graph, ctx, vars_, node_ns, rel, layer)
