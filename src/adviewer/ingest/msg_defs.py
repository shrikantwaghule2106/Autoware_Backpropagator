"""Parse ROS 2 .msg definition files into MsgType records."""
from __future__ import annotations

import re
from pathlib import Path

from adviewer.model import Graph, MsgField, MsgType

_ARRAY = re.compile(r"^(?P<base>[\w/]+)(?:\[(?P<len>\d*)\])?$")
_BUILTIN = {
    "bool", "byte", "char", "float32", "float64", "int8", "uint8", "int16", "uint16",
    "int32", "uint32", "int64", "uint64", "string", "wstring",
}


def parse_msg_file(path: Path, pkg: str, graph: Graph, rel: str = "") -> MsgType | None:
    path = Path(path)
    msg = MsgType(id=f"{pkg}/msg/{path.stem}", source_file=rel or str(path))
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    header = []
    for raw in lines:  # the leading comment block describes the message
        if not raw.strip().startswith("#"):
            break
        header.append(raw.strip().lstrip("#").strip())
    msg.description = " ".join(h for h in header if h)
    for raw in lines:
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if "=" in line and not line.startswith("string") and " " in line.split("=")[0]:
            # constant: TYPE NAME=VALUE
            lhs, val = line.split("=", 1)
            parts = lhs.split()
            if len(parts) == 2:
                msg.constants[parts[1].strip()] = val.strip()
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        ftype, fname = parts[0], parts[1]
        if "=" in fname:  # string constant form
            msg.constants[fname.split("=")[0]] = fname.split("=", 1)[1]
            continue
        m = _ARRAY.match(ftype.split("<=")[0])
        if not m:
            continue
        base = m.group("base")
        if base not in _BUILTIN and "/" not in base:
            base = f"{pkg}/{base}"  # same-package reference
        if "/" in base and "/msg/" not in base:
            p, n = base.split("/", 1)
            base = f"{p}/msg/{n}"
        length = m.group("len")
        msg.fields.append(
            MsgField(
                name=fname,
                type=base,
                is_array="[" in ftype,
                array_len=int(length) if length else None,
            )
        )
    graph.msg_types[msg.id] = msg
    return msg


def msg_files(pkg_dir: Path) -> list[Path]:
    """A package's .msg files: msg/ in an install space; in a source tree also msg/ folders
    further down (autoware_adapi_v1_msgs keeps system/msg/MrmState.msg, installed as msg/)."""
    return sorted(f for f in Path(pkg_dir).rglob("*.msg") if f.parent.name == "msg")


def ingest_msg_dir(pkg_dir: Path, pkg: str, graph: Graph, root: Path | None = None) -> int:
    n = 0
    for f in msg_files(pkg_dir):
        rel = f.relative_to(root).as_posix() if root else f.as_posix()
        if parse_msg_file(f, pkg, graph, rel):
            n += 1
    return n
