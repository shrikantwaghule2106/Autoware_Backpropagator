"""Expected rates and timer periods, each labelled with its source (FR-6).

- timer:   a C++ timer with a literal period in the node's package (cpp_source.scan_timers);
           a package may hold several nodes, so the note names the package.
- param:   a node parameter whose *name* says it is a rate or period. Only explicit names
           (update_rate, publish_rate, *_frequency, *_hz, *_period, …) — names such as
           steer_rate_lim are not rates in Hz and are not matched.
- monitor: topic_state_monitor thresholds for the topic it watches (warn_rate, error_rate,
           timeout), from its parameters.
"""
from __future__ import annotations

import re

from adviewer.model import Graph, Rate

from .cpp_source import Timer

_HZ_NAMES = re.compile(
    r"^(update_rate|publish_rate|output_rate|loop_rate|rate|frequency|update_hz|publish_hz)$"
    r"|_(frequency|hz|rate_hz)$"
)
_PERIOD_S = re.compile(r"^(update_period|publish_period|timer_period|control_period|period)(_sec|_s)?$")
_PERIOD_MS = re.compile(r"(^|_)period_ms$")


def _number(text: str) -> float | None:
    try:
        value = float(text)
    except ValueError:
        return None
    return value if value > 0 else None


def apply_rates(graph: Graph, timers: dict[str, list[tuple[str, Timer]]]) -> None:
    for node in graph.nodes.values():
        seen = set()
        for file, timer in timers.get(node.package, []):
            key = (round(timer.hz, 6), file, timer.line)
            if key in seen:
                continue
            seen.add(key)
            node.rates.append(Rate(hz=round(timer.hz, 3), kind="timer",
                                   evidence=f"{file}:{timer.line}",
                                   note=f"timer {timer.as_written} in package {node.package}"))
    monitors: dict[str, dict[str, tuple[str, str]]] = {}
    for prm in graph.params:
        owner = graph.nodes.get(prm.node)
        if owner is None:
            continue
        if owner.package == "autoware_topic_state_monitor":
            monitors.setdefault(owner.id, {})[prm.name] = (prm.value, prm.evidence)
            continue
        leaf = prm.name.rsplit(".", 1)[-1]
        hz = None
        if _HZ_NAMES.search(leaf):
            hz = _number(prm.value)
        elif _PERIOD_S.match(leaf) and (v := _number(prm.value)):
            hz = 1.0 / v
        elif _PERIOD_MS.search(leaf) and (v := _number(prm.value)):
            hz = 1000.0 / v
        if hz is not None:
            owner.rates.append(Rate(hz=round(hz, 3), kind="param", evidence=prm.evidence,
                                   note=f"parameter {prm.name} = {prm.value}"))
    for node_id, values in monitors.items():
        topic_name = values.get("topic", ("", ""))[0]
        topic = graph.topics.get(topic_name)
        if topic is None:
            continue
        warn = values.get("warn_rate", ("", ""))
        error = values.get("error_rate", ("", ""))[0]
        timeout = values.get("timeout", ("", ""))[0]
        topic.rates.append(Rate(
            hz=_number(warn[0]), kind="monitor", evidence=warn[1] or values["topic"][1],
            note=f"{node_id}: warn below {warn[0]} Hz, error below {error} Hz, timeout {timeout} s",
        ))
