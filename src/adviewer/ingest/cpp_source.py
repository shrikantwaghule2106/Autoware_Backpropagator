"""Scan C++ source for ROS 2 publishers and subscriptions (read as text, never compiled; D22).

Finds create_publisher<T>("name", qos) and create_subscription<T>("name", qos), also split
across lines and called via this-> / node->. Only literal topic names count.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .package_xml import PackageInfo, _is_non_runtime

_CALL = re.compile(r'create_(publisher|subscription)\s*<([^;()"]*?)>\s*\(\s*"([^"]+)"\s*,', re.DOTALL)
# `using Alias = pkg::msg::Type;` and `typedef pkg::msg::Type Alias;` (F7b)
_USING = re.compile(r"\busing\s+(\w+)\s*=\s*(?:typename\s+)?((?:::)?[\w:]+::msg::\w+)\s*;")
_TYPEDEF = re.compile(r"\btypedef\s+((?:::)?[\w:]+::msg::\w+)\s+(\w+)\s*;")


@dataclass
class Endpoint:
    kind: str  # "publisher" or "subscription"
    name: str  # topic name as written in C++, e.g. "~/input/twist"
    msg_type: str  # "pkg/msg/Type" when written as pkg::msg::Type (or a package alias), else ""
    qos: str  # the QoS argument as written, e.g. "rclcpp::QoS{10}"
    line: int  # line of the create_… call
    template: str = ""  # the template argument as written, e.g. "TwistStamped"


def _msg_type(template_arg: str) -> str:
    """'pkg/msg/Type' for a template argument written as pkg::msg::Type; '' otherwise."""
    t = "".join(template_arg.split())
    t = t.lstrip(":")
    if "::msg::" in t:
        return t.replace("::", "/")
    return ""


def _next_argument(text: str, start: int) -> str:
    """The C++ argument starting at `start`, up to the next top-level ',' or closing bracket."""
    depth = 0
    i = start
    while i < len(text):
        ch = text[i]
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            if depth == 0:
                break
            depth -= 1
        elif ch == "," and depth == 0:
            break
        i += 1
    return " ".join(text[start:i].split())


def _arguments(text: str, start: int, count: int) -> list[str]:
    """Up to `count` top-level arguments of the call whose '(' ends just before `start`."""
    args: list[str] = []
    depth = 0
    begin = start
    for i in range(start, len(text)):
        ch = text[i]
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            if depth == 0:
                args.append(text[begin:i])
                break
            depth -= 1
        elif ch == "," and depth == 0:
            args.append(text[begin:i])
            begin = i + 1
            if len(args) == count:
                break
    return [" ".join(a.split()) for a in args]


def scan_cpp(text: str) -> list[Endpoint]:
    """All literal publishers/subscriptions in one C++ file's text."""
    found: list[Endpoint] = []
    for m in _CALL.finditer(text):
        found.append(
            Endpoint(
                kind=m.group(1),
                name=m.group(3),
                msg_type=_msg_type(m.group(2)),
                qos=_next_argument(text, m.end()),
                line=text.count("\n", 0, m.start()) + 1,
                template="".join(m.group(2).split()),
            )
        )
    return found

# Timers with a literal period (FR-6): create_wall_timer(100ms | std::chrono::milliseconds(100)
# | std::chrono::duration<double>(0.1) | 1s, …), rclcpp::create_timer(node, clock, <period>, …),
# and rclcpp::Rate(10.0).
_TIMER = re.compile(r"create_(?:wall_)?timer\s*\(")
_PERIOD = re.compile(
    r"^(?:std::chrono::)?(?:(milliseconds|seconds|microseconds|nanoseconds)\s*\(\s*([\d.]+)\s*\)"
    r"|duration\s*<\s*double\s*>\s*\(\s*([\d.]+)\s*\)"
    r"|([\d.]+)\s*(ms|s|us|ns)\b)"
)
_RATE = re.compile(r"rclcpp::Rate\s*\(\s*([\d.]+)\s*\)")
_UNIT = {"milliseconds": 1e-3, "ms": 1e-3, "seconds": 1.0, "s": 1.0, "microseconds": 1e-6,
         "us": 1e-6, "nanoseconds": 1e-9, "ns": 1e-9}


@dataclass
class Timer:
    hz: float
    line: int
    as_written: str


def _period_seconds(arg: str) -> float | None:
    m = _PERIOD.match(arg)
    if not m:
        return None
    if m.group(1):
        return float(m.group(2)) * _UNIT[m.group(1)]
    if m.group(3):
        return float(m.group(3))
    return float(m.group(4)) * _UNIT[m.group(5)]


def scan_timers(text: str) -> list[Timer]:
    """Timers whose period is a literal; a period held in a variable is skipped."""
    found: list[Timer] = []
    for m in _TIMER.finditer(text):
        # wall timer: 1st argument is the period; create_timer(node, clock, period, …): 3rd
        for arg in _arguments(text, m.end(), 3):
            seconds = _period_seconds(arg)
            if seconds:
                found.append(Timer(1.0 / seconds, text.count("\n", 0, m.start()) + 1, arg))
                break
    for m in _RATE.finditer(text):
        hz = float(m.group(1))
        if hz > 0:
            found.append(Timer(hz, text.count("\n", 0, m.start()) + 1, m.group(0)))
    return found


def index_timers(packages: dict[str, PackageInfo]) -> dict[str, list[tuple[str, Timer]]]:
    """Package name -> [(file, timer)] with literal periods (test/example folders skipped)."""
    index: dict[str, list[tuple[str, Timer]]] = {}
    for name, info in packages.items():
        found: list[tuple[str, Timer]] = []
        for f in sorted(info.path.rglob("*.cpp")):
            if not f.is_file() or _is_non_runtime(f, info.path):
                continue
            rel = f"{info.rel_path}/{f.relative_to(info.path).as_posix()}"
            found.extend((rel, t) for t in scan_timers(f.read_text(encoding="utf-8", errors="replace")))
        if found:
            index[name] = found
    return index


def scan_aliases(text: str) -> dict[str, str]:
    """Alias -> 'pkg/msg/Type' from `using` / `typedef` lines in one file."""
    aliases = {m.group(1): _msg_type(m.group(2)) for m in _USING.finditer(text)}
    aliases.update({m.group(2): _msg_type(m.group(1)) for m in _TYPEDEF.finditer(text)})
    return aliases


def index_endpoints(packages: dict[str, PackageInfo]) -> dict[str, list[tuple[str, Endpoint]]]:
    """Package name -> [(file, endpoint)] from each package's C++ (test/example folders skipped).

    A template argument that is an alias declared anywhere in the same package
    (`using Twist = geometry_msgs::msg::TwistStamped;`) gets that type; an alias
    declared twice with different types stays unresolved.
    """
    index: dict[str, list[tuple[str, Endpoint]]] = {}
    for name, info in packages.items():
        found: list[tuple[str, Endpoint]] = []
        aliases: dict[str, str] = {}
        clashes: set[str] = set()
        for f in sorted(info.path.rglob("*")):
            if f.suffix not in (".cpp", ".hpp", ".h") or not f.is_file() or _is_non_runtime(f, info.path):
                continue
            text = f.read_text(encoding="utf-8", errors="replace")
            for alias, typ in scan_aliases(text).items():
                if aliases.get(alias, typ) != typ:
                    clashes.add(alias)
                aliases[alias] = typ
            rel = f"{info.rel_path}/{f.relative_to(info.path).as_posix()}"
            for ep in scan_cpp(text):
                found.append((rel, ep))
        for _, ep in found:
            short = ep.template.rsplit("::", 1)[-1]
            if not ep.msg_type and short in aliases and short not in clashes:
                ep.msg_type = aliases[short]
        if found:
            index[name] = found
    return index
