"""Launch conditions (`if=` / `unless=`) and `$(eval …)`, evaluated without running Python (F10).

Rules mirror ROS 2 Jazzy `launch` 3.4:
- a condition is true if its text, stripped and lower-cased, is 'true' or '1', false if it
  is 'false' or '0'; anything else is an error in ROS (here: unknown)
  (launch/conditions/evaluate_condition_expression_impl.py);
- `$(eval expr)` is `str(eval(expr))` (launch/substitutions/python_expression.py).
Here `expr` is evaluated by a small whitelist over Python's `ast` — literals, lists,
comparisons, and/or/not, `a if c else b`, unary minus, constant indexing, `len`/`set`/`list`, set
intersection/union, and the string methods split/strip/lower/upper/startswith/endswith
(Autoware 1.9.0 uses `len`, `.split`, `[0]`, `list(set(…).intersection(…))`) — never by
`eval`. Anything else is unknown (None), never guessed.
"""
from __future__ import annotations

import ast
import operator
from typing import Any

_STR_METHODS = {"split", "strip", "lower", "upper", "startswith", "endswith"}

_COMPARE = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
    ast.In: lambda a, b: a in b,
    ast.NotIn: lambda a, b: a not in b,
}


class _Unknown(Exception):
    """The expression uses something outside the whitelist."""


def _value(node: ast.AST) -> Any:
    if isinstance(node, ast.Expression):
        return _value(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (str, int, float, bool)):
        return node.value
    if isinstance(node, ast.Name) and node.id in ("True", "False"):
        return node.id == "True"
    if isinstance(node, ast.BoolOp):  # short-circuit and return an operand, as Python does
        result = _value(node.values[0])
        for v in node.values[1:]:
            if (isinstance(node.op, ast.And) and not result) or (isinstance(node.op, ast.Or) and result):
                return result
            result = _value(v)
        return result
    if isinstance(node, ast.UnaryOp):
        operand = _value(node.operand)
        if isinstance(node.op, ast.Not):
            return not operand
        if isinstance(node.op, ast.USub) and isinstance(operand, (int, float)):
            return -operand
        raise _Unknown
    if isinstance(node, ast.Compare):
        left = _value(node.left)
        for op, right_node in zip(node.ops, node.comparators):
            fn = _COMPARE.get(type(op))
            if fn is None:
                raise _Unknown
            right = _value(right_node)
            try:
                if not fn(left, right):
                    return False
            except TypeError as exc:  # e.g. '3' >= 2 — an error in ROS too
                raise _Unknown from exc
            left = right
        return True
    if isinstance(node, ast.IfExp):  # a if test else b (lidar_centerpoint.launch.xml:24)
        return _value(node.body) if _value(node.test) else _value(node.orelse)
    if isinstance(node, (ast.List, ast.Tuple)):
        return [_value(e) for e in node.elts]
    if isinstance(node, ast.Subscript):
        container, index = _value(node.value), _value(node.slice)
        if isinstance(container, (str, list)) and isinstance(index, int):
            try:
                return container[index]
            except IndexError as exc:
                raise _Unknown from exc
        raise _Unknown
    if isinstance(node, ast.Call) and not node.keywords:
        return _call(node)
    raise _Unknown


def _call(node: ast.Call) -> Any:
    """Whitelisted calls only: len/set/list, a few str methods, set intersection/union."""
    args = [_value(a) for a in node.args]
    iterable = (str, list, set)
    if isinstance(node.func, ast.Name) and len(args) == 1 and isinstance(args[0], iterable):
        if node.func.id == "len":
            return len(args[0])
        if node.func.id == "set":
            return set(args[0])
        if node.func.id == "list":  # sorted: a set's order is arbitrary, membership is not
            return sorted(args[0]) if isinstance(args[0], set) else list(args[0])
    if isinstance(node.func, ast.Attribute):
        target, method = _value(node.func.value), node.func.attr
        str_args = len(args) <= 1 and all(isinstance(a, str) for a in args)
        if isinstance(target, str) and method in _STR_METHODS and str_args:
            return getattr(target, method)(*args)
        set_op = method in ("intersection", "union") and len(args) == 1
        if isinstance(target, set) and set_op and isinstance(args[0], iterable):
            return getattr(target, method)(args[0])
    raise _Unknown


def eval_expression(text: str) -> str | None:
    """The string ROS would produce for the Python expression `text`, or None if undecidable."""
    if "$(" in text:
        return None  # an inner substitution stayed unresolved
    try:
        result = _value(ast.parse(text.strip(), mode="eval"))
    except (SyntaxError, _Unknown, RecursionError):
        return None
    return str(result)


def evaluate_condition(text: str) -> bool | None:
    """Truth of an already-substituted condition text, or None if ROS would not accept it."""
    word = text.strip().lower()
    if word in ("true", "1"):
        return True
    if word in ("false", "0"):
        return False
    return None
