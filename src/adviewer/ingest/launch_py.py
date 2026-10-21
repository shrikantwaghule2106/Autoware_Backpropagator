"""Read ROS 2 Python launch files (.launch.py) statically, never executed (D21).

Extracts Node(...) and ComposableNode(...) calls whose arguments can be resolved without
running the file, also inside OpaqueFunction bodies. Computed values are reported, never
guessed. Variables assigned exactly once in the file are followed (F11c).
"""
from __future__ import annotations

import ast
import posixpath
from pathlib import Path

from adviewer.model import Gate, Graph, Layer, Node, Param, ParamKind

from .conditions import eval_expression, evaluate_condition
from .launch_xml import (
    LaunchContext,
    _add_remap,
    _join_ns,
    add_param_file,
    register_gate,
)


def _call_name(call: ast.Call) -> str:
    """'Node' for Node(...) and for launch_ros.actions.Node(...); '' for anything else."""
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return ""


def _literal_str(expr: ast.expr | None) -> str | None:
    """The text if expr is a literal string, else None (computed or missing)."""
    if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
        return expr.value
    return None

class _Scope(dict[str, str]):
    """Launch configurations, plus what a file knows statically: its variables assigned
    exactly once (F11c) and the package index (for FindPackageShare)."""

    def __init__(self, values: dict[str, str], tree: ast.AST | None = None,
                 ctx: LaunchContext | None = None):
        super().__init__(values)
        self.ctx = ctx
        self.assigns: dict[str, ast.expr] = {}
        self.resolving: set[str] = set()  # names being resolved, against self-reference
        if tree is None:
            return
        counts: dict[str, int] = {}
        for stmt in ast.walk(tree):
            pairs: list[tuple[ast.expr, ast.expr]] = []
            if isinstance(stmt, ast.Assign):
                pairs = [(t, stmt.value) for t in stmt.targets]
            elif isinstance(stmt, ast.AnnAssign) and stmt.value is not None:
                pairs = [(stmt.target, stmt.value)]
            for target, value in pairs:
                if isinstance(target, ast.Name):
                    counts[target.id] = counts.get(target.id, 0) + 1
                    self.assigns[target.id] = value
            for node in ast.walk(stmt) if isinstance(stmt, (ast.For, ast.With)) else []:
                if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                    counts[node.id] = counts.get(node.id, 0) + 2  # loop variables: never fixed
        self.assigns = {n: e for n, e in self.assigns.items() if counts.get(n) == 1}


def _resolve_name(name: str, vars_: dict[str, str]) -> str | None:
    if not isinstance(vars_, _Scope) or name not in vars_.assigns or name in vars_.resolving:
        return None
    vars_.resolving.add(name)
    try:
        return _resolve(vars_.assigns[name], vars_)
    finally:
        vars_.resolving.discard(name)


def _resolve_call(expr: ast.Call, vars_: dict[str, str]) -> str | None:
    kind = _call_name(expr)
    args = expr.args
    if kind == "perform" and isinstance(expr.func, ast.Attribute):
        return _resolve(expr.func.value, vars_)
    if kind == "LaunchConfiguration" and args:
        key = _literal_str(args[0])
        return vars_.get(key) if key is not None else None
    if kind == "PythonExpression" and args:
        text = _resolve(args[0], vars_)
        return eval_expression(text) if text is not None else None
    if kind in ("FindPackageShare", "get_package_share_directory") and args:
        pkg = _resolve(args[0], vars_)
        ctx = vars_.ctx if isinstance(vars_, _Scope) else None
        share = ctx.find_pkg_share(pkg) if ctx and pkg else None
        return str(share) if share else None
    if kind == "ParameterFile":
        target = args[0] if args else next(
            (k.value for k in expr.keywords if k.arg == "param_file"), None
        )
        return _resolve(target, vars_)
    if kind == "str" and len(args) == 1:
        return _resolve(args[0], vars_)
    if kind == "PathJoinSubstitution" and len(args) == 1 and isinstance(args[0], (ast.List, ast.Tuple)):
        parts = [_resolve(e, vars_) for e in args[0].elts]
        return None if None in parts else "/".join(p for p in parts if p is not None)
    if kind == "join" and isinstance(expr.func, ast.Attribute):
        sep = _literal_str(expr.func.value)  # "/".join([...])
        if sep is not None and len(args) == 1 and isinstance(args[0], (ast.List, ast.Tuple)):
            parts = [_resolve(e, vars_) for e in args[0].elts]
            return None if None in parts else sep.join(p for p in parts if p is not None)
        if ast.unparse(expr.func.value) == "os.path":  # os.path.join(a, b, ...)
            parts = [_resolve(a, vars_) for a in args]
            return None if None in parts or not parts else posixpath.join(*[p for p in parts if p is not None])
    return None


def _resolve(expr: ast.expr | None, vars_: dict[str, str]) -> str | None:
    """The text expr stands for, if it is knowable without running the file; else None.

    Literals, LaunchConfiguration("x") (also .perform(context)), a + b, [piece, ...]
    (ROS launch joins a list of substitutions: launch/utilities/perform_substitutions_impl.py),
    f-strings, PythonExpression, FindPackageShare / get_package_share_directory,
    PathJoinSubstitution, os.path.join, "sep".join, ParameterFile, and names assigned once.
    """
    text = _literal_str(expr)
    if text is not None:
        return text
    if isinstance(expr, ast.Name):
        return _resolve_name(expr.id, vars_)
    if isinstance(expr, ast.JoinedStr):
        out = ""
        for part in expr.values:
            if isinstance(part, ast.Constant):
                out += str(part.value)
                continue
            if not isinstance(part, ast.FormattedValue) or part.format_spec is not None:
                return None
            piece = _resolve(part.value, vars_)
            if piece is None:
                return None
            out += piece
        return out
    if isinstance(expr, ast.Call):
        return _resolve_call(expr, vars_)
    if isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.Add):
        left = _resolve(expr.left, vars_)
        right = _resolve(expr.right, vars_)
        if left is None or right is None:
            return None
        return left + right
    if isinstance(expr, ast.List):
        joined = ""
        for element in expr.elts:
            piece = _resolve(element, vars_)
            if piece is None:
                return None
            joined += piece
        return joined
    return None

def _declared_defaults(
    tree: ast.AST, scope: _Scope, graph: Graph | None = None, rel: str = "", package: str = ""
) -> dict[str, str]:
    """Defaults of DeclareLaunchArgument(name, default_value=...) and of Autoware's per-file
    helper add_launch_arg(name, default) (283 uses in Autoware 1.9.0), resolved as far as
    `_resolve` can (literals, PathJoinSubstitution(FindPackageShare…), …).
    With `graph`, each one is also indexed as an overridable launch argument (FR-7)."""
    defaults: dict[str, str] = {}
    for call in ast.walk(tree):
        if not isinstance(call, ast.Call) or _call_name(call) not in ("DeclareLaunchArgument", "add_launch_arg"):
            continue
        kw = {k.arg: k.value for k in call.keywords if k.arg}
        name = _literal_str(call.args[0]) if call.args else _literal_str(kw.get("name"))
        default = _resolve(call.args[1] if len(call.args) > 1 else kw.get("default_value"), scope)
        if name is not None and default is not None:
            defaults[name] = default
            if graph is not None and name not in scope:  # an includer's value overrides it
                graph.params.append(
                    Param(name=name, value=default, kind=ParamKind.launch_arg,
                          evidence=f"{rel}:{call.lineno}", package=package)
                )
    return defaults


def _literal_value(expr: ast.expr, vars_: dict[str, str]) -> str | None:
    """Text of a parameter value: resolvable string, number or bool (as ROS writes it)."""
    if isinstance(expr, ast.Constant) and isinstance(expr.value, bool):
        return "true" if expr.value else "false"
    if isinstance(expr, ast.Constant) and isinstance(expr.value, (int, float)):
        return str(expr.value)
    return _resolve(expr, vars_)


def _node_params(
    call: ast.Call, node: Node, graph: Graph, ctx: LaunchContext, rel: str, vars_: dict[str, str]
) -> None:
    """parameters=[{name: value}, "file.yaml", …]: index what can be read statically."""
    params = next((k.value for k in call.keywords if k.arg == "parameters"), None)
    if not isinstance(params, ast.List):
        return
    for item in params.elts:
        where = f"{rel}:{item.lineno}"
        if isinstance(item, ast.Dict):
            for key, value in zip(item.keys, item.values):
                name = _literal_str(key) if key is not None else None
                text = _literal_value(value, vars_) if name is not None else None
                if name is None or text is None:
                    continue
                node.params[name] = text
                graph.params.append(
                    Param(node=node.id, name=name, value=text, kind=ParamKind.launch_param,
                          evidence=where, package=ctx.package_name(rel))
                )
            continue
        path = _resolve(item, vars_)
        if path is not None and path.endswith((".yaml", ".yml")):
            add_param_file(graph, ctx, node, path, where)


def _condition(call: ast.Call, vars_: dict[str, str]) -> bool | None:
    """Value of a call's condition= (F10): True/False, or None if absent or undecidable.

    IfCondition(x) / UnlessCondition(x) follow the ROS truth rule on x's text;
    LaunchConfigurationEquals(name, value) / NotEquals compare the configuration's text.
    """
    cond = next((k.value for k in call.keywords if k.arg == "condition"), None)
    if not isinstance(cond, ast.Call) or not cond.args:
        return None
    kind = _call_name(cond)
    if kind in ("IfCondition", "UnlessCondition"):
        text = _resolve(cond.args[0], vars_)
        truth = evaluate_condition(text) if text is not None else None
        if truth is None:
            return None
        return truth if kind == "IfCondition" else not truth
    if kind in ("LaunchConfigurationEquals", "LaunchConfigurationNotEquals") and len(cond.args) == 2:
        name, value = _literal_str(cond.args[0]), _resolve(cond.args[1], vars_)
        if name is None or value is None or name not in vars_:
            return None
        same = vars_[name] == value
        return same if kind == "LaunchConfigurationEquals" else not same
    return None


def _skipped_by_conditions(
    tree: ast.AST, graph: Graph, rel: str, vars_: dict[str, str], ctx: LaunchContext | None = None,
    gated: dict[int, list[Gate]] | None = None,
) -> set[int]:
    """ids of every call nested in a call whose condition= is false. With `ctx`, each
    condition that reads launch configurations is recorded as a switch gate, and `gated`
    maps the nested Node calls of running gates to their gates."""
    skipped: set[int] = set()
    for call in ast.walk(tree):
        if not isinstance(call, ast.Call) or not any(k.arg == "condition" for k in call.keywords):
            continue
        value = _condition(call, vars_)
        if value is None:
            graph.warn(f"{rel}:{call.lineno}: {_call_name(call)} condition not resolved, kept")
        elif value is False:
            skipped.update(id(inner) for inner in ast.walk(call))
        if ctx is None:
            continue
        cond = next(k.value for k in call.keywords if k.arg == "condition")
        names = [k for c in ast.walk(cond) if isinstance(c, ast.Call) and _call_name(c) == "LaunchConfiguration"
                 and c.args and (k := _literal_str(c.args[0])) is not None]
        if not names:
            continue
        inner_names = [n for c in ast.walk(call) if isinstance(c, ast.Call) and _call_name(c) in ("Node", "ComposableNode")
                       for k in c.keywords if k.arg == "name" and (n := _literal_str(k.value))]
        gate = register_gate(ctx, vars_, list(dict.fromkeys(names)), Gate(
            evidence=f"{rel}:{call.lineno}", tag=_call_name(call), condition=ast.unparse(cond),
            on=value, target=", ".join(inner_names[:6])))
        if value is not False and gated is not None:
            for inner in ast.walk(call):
                if isinstance(inner, ast.Call) and _call_name(inner) in ("Node", "ComposableNode"):
                    gated.setdefault(id(inner), []).append(gate)
    return skipped


def _resolve_pair(expr: ast.expr, vars_: dict[str, str]) -> tuple[str, str] | None:
    """(from, to) if expr is a 2-item tuple/list of literal strings, else None."""
    if not isinstance(expr, (ast.Tuple, ast.List)) or len(expr.elts) != 2:
        return None
    frm = _resolve(expr.elts[0], vars_)
    to = _resolve(expr.elts[1], vars_)
    if frm is None or to is None:
        return None
    return (frm, to)

def _node_from_call(
    call: ast.Call, graph: Graph, ctx: LaunchContext, ns: str, rel: str, layer: Layer, vars_: dict[str, str]
) -> str | None:
    """Add the node of a Node/ComposableNode call; returns its full name, or None if skipped."""
    kind = _call_name(call)
    args = {kw.arg: kw.value for kw in call.keywords if kw.arg}
    where = f"{rel}:{call.lineno}"

    name = _resolve(args.get("name"), vars_)
    if name is None:
        graph.warn(f"{where}: {kind} name not resolved (computed or missing), skipped")
        return None
    node_ns = ns
    if "namespace" in args:
        sub_ns = _resolve(args["namespace"], vars_)
        if sub_ns is None:
            graph.warn(f"{where}: {kind} '{name}' namespace not resolved, skipped")
            return None
        node_ns = _join_ns(ns, sub_ns)
    fqn = _join_ns(node_ns, name)

    pkg = _resolve(args.get("package"), vars_) or ""
    exe = _resolve(args.get("plugin" if kind == "ComposableNode" else "executable"), vars_) or ""
    pkg_layer = ctx.packages[pkg].layer if pkg in ctx.packages else Layer.unknown
    node = graph.add_node(
        Node(id=fqn, package=pkg, executable=exe, namespace=node_ns,
             layer=pkg_layer if pkg_layer != Layer.unknown else layer, source_file=rel)
    )

    _node_params(call, node, graph, ctx, rel, vars_)
    remaps = args.get("remappings")
    if remaps is None:
        return fqn
    if not isinstance(remaps, ast.List):
        graph.warn(f"{where}: {kind} '{fqn}' remappings not resolved, skipped")
        return fqn
    for entry in remaps.elts:
        pair = _resolve_pair(entry, vars_)
        if pair is None:
            graph.warn(f"{rel}:{entry.lineno}: remap on {fqn} not resolved, skipped")
            continue
        frm, to = pair
        _add_remap(graph, ctx, node, frm, to, f"{rel}:{entry.lineno}: remap from='{frm}' to='{to}'")
    return fqn




def parse_launch_py(
    path: Path,
    graph: Graph,
    ctx: LaunchContext,
    namespace: str = "/",
    layer: Layer = Layer.unknown,
    vars_: dict[str, str] | None = None) -> None:
    """Add the literal nodes, topics and remap edges of one .launch.py file to graph."""
    path = Path(path)
    rel = ctx.rel(path)
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
    except (SyntaxError, OSError) as exc:
        graph.warn(f"python launch parse error {rel}: {exc}")
        return
    known = _Scope(dict(vars_ or {}), tree, ctx)  # the includer's values win over defaults
    for name, value in _declared_defaults(tree, known, graph, rel, ctx.package_name(rel)).items():
        known.setdefault(name, value)
    if ctx.emulators:
        from .launch_emulators import emulate

        emulate(path, graph, ctx, namespace, layer, known, ctx.emulators)
    gated: dict[int, list[Gate]] = {}
    skipped = _skipped_by_conditions(tree, graph, rel, known, ctx, gated)
    for call in ast.walk(tree):
        if not isinstance(call, ast.Call) or id(call) in skipped:
            continue
        if _call_name(call) in ("Node", "ComposableNode"):
            fqn = _node_from_call(call, graph, ctx, namespace, rel, layer, known)
            if fqn is None:
                continue
            for gate in gated.get(id(call), []) + ctx.gate_stack:  # XML gates around this include too
                if fqn not in gate.nodes:
                    gate.nodes.append(fqn)
