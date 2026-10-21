"""Read a QoS argument as written in C++ into its policies (F7b), without compiling.

Presets from ROS 2 Jazzy `rmw/qos_profiles.h` and `rclcpp/qos.hpp`:
  QoS(n) / QoS{n} / n / QoS(KeepLast(n))  reliable, volatile, keep_last n  (rmw default)
  SensorDataQoS()                         best_effort, volatile, keep_last 5
  ParametersQoS()                         reliable, volatile, keep_last 1000
  ServicesQoS()                           reliable, volatile, keep_last 10
  SystemDefaultsQoS()                     system_default
  BestAvailableQoS()                      best_available, keep_last 10
Modifiers: .keep_last(n) .keep_all() .reliable() .best_effort() .transient_local()
.durability_volatile() .reliability(…BestEffort|Reliable) .durability(…TransientLocal|Volatile).
A QoS held in a variable is unknown: only the modifiers written at the call are reported.
"""
from __future__ import annotations

import re

_PRESETS: list[tuple[re.Pattern[str], dict[str, str]]] = [
    (re.compile(r"SensorDataQoS\s*[({]"), {"reliability": "best_effort", "durability": "volatile", "history": "keep_last", "depth": "5"}),
    (re.compile(r"ParametersQoS\s*[({]"), {"reliability": "reliable", "durability": "volatile", "history": "keep_last", "depth": "1000"}),
    (re.compile(r"ServicesQoS\s*[({]"), {"reliability": "reliable", "durability": "volatile", "history": "keep_last", "depth": "10"}),
    (re.compile(r"SystemDefaultsQoS\s*[({]"), {"reliability": "system_default", "durability": "system_default", "history": "system_default", "depth": ""}),
    (re.compile(r"BestAvailableQoS\s*[({]"), {"reliability": "best_available", "durability": "best_available", "history": "keep_last", "depth": "10"}),
]
_DEFAULT = {"reliability": "reliable", "durability": "volatile", "history": "keep_last"}
_QOS_DEPTH = re.compile(r"^(?:rclcpp::)?QoS\s*[({]\s*(?:(?:rclcpp::)?KeepLast\s*\(\s*)?(\w+)")
_QOS_KEEP_ALL = re.compile(r"^(?:rclcpp::)?QoS\s*[({]\s*(?:rclcpp::)?KeepAll\s*\(")
_INT = re.compile(r"^\d+$")


def interpret_qos(raw: str) -> dict[str, str]:
    """Policies known from the text; always includes `as_written`."""
    text = " ".join(raw.split())
    out: dict[str, str] = {}
    for pattern, values in _PRESETS:
        if pattern.match(text.removeprefix("rclcpp::")):
            out.update(values)
            break
    else:
        if _INT.match(text):
            out.update(_DEFAULT, depth=text)
        elif _QOS_KEEP_ALL.match(text):
            out.update(_DEFAULT, history="keep_all", depth="")
        elif m := _QOS_DEPTH.match(text):
            out.update(_DEFAULT, depth=m.group(1) if _INT.match(m.group(1)) else "?")
    for m in re.finditer(r"\.(\w+)\s*\(([^()]*)\)", text):
        method, arg = m.group(1), m.group(2)
        if method == "keep_last":
            out.update(history="keep_last", depth=arg.strip() if _INT.match(arg.strip()) else "?")
        elif method == "keep_all":
            out.update(history="keep_all", depth="")
        elif method == "reliable" or (method == "reliability" and "Reliable" in arg):
            out["reliability"] = "reliable"
        elif method == "best_effort" or (method == "reliability" and "BestEffort" in arg):
            out["reliability"] = "best_effort"
        elif method == "transient_local" or (method == "durability" and "TransientLocal" in arg):
            out["durability"] = "transient_local"
        elif method == "durability_volatile" or (method == "durability" and "Volatile" in arg):
            out["durability"] = "volatile"
    out["as_written"] = text
    return out


def incompatible(pub: dict[str, str], sub: dict[str, str]) -> list[str]:
    """Reasons a subscription cannot receive from a publisher (ROS 2 request-vs-offered rules)."""
    reasons = []
    if pub.get("reliability") == "best_effort" and sub.get("reliability") == "reliable":
        reasons.append("publisher best_effort, subscriber requires reliable")
    if pub.get("durability") == "volatile" and sub.get("durability") == "transient_local":
        reasons.append("publisher volatile, subscriber requires transient_local")
    return reasons
