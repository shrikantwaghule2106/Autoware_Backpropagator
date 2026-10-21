"""Topic and node descriptions from Autoware's own package READMEs (topic docs).

Most Autoware packages document their interface in README.md: a "Purpose" (or Overview)
section and "Inputs / Outputs" tables `| Name | Type | Description |` (134 such READMEs in
the 1.9.0 source companion). The text is copied as written, with file:line — adviewer does
not write descriptions of its own (no unverified Autoware facts).

A table row is matched to a graph topic through the package's nodes: the row's name is the
topic itself (`/system/fail_safe/mrm_state`) or the name the node uses before its remap
(`~/input/odometry` → whatever the launch file remapped it to).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from adviewer.model import Direction, Doc, Graph

from .package_xml import PackageInfo

_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_PURPOSE = re.compile(r"purpose|overview|abstract|role|description", re.IGNORECASE)
_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_REMAP_FROM = re.compile(r"remap from='([^']*)'")


@dataclass
class Row:
    name: str
    role: str  # "input" / "output" / ""
    type: str
    text: str
    line: int


def _clean(cell: str) -> str:
    return " ".join(_LINK.sub(r"\1", cell).replace("`", "").replace("<br>", " ").split())


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def parse_readme(text: str) -> tuple[tuple[str, int] | None, list[Row]]:
    """(purpose paragraph and its line, interface table rows) of one README."""
    lines = text.splitlines()
    purpose: tuple[str, int] | None = None
    rows: list[Row] = []
    heading, in_purpose, para_line = "", False, 0
    para: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        h = _HEADING.match(line)
        if h:
            if in_purpose and para and purpose is None:
                purpose = (" ".join(para), para_line)
            heading = h.group(2)
            in_purpose = purpose is None and bool(_PURPOSE.search(heading))
            para = []
            i += 1
            continue
        if in_purpose and purpose is None:
            if line.strip() and not line.lstrip().startswith(("|", "!", "<", "```")):
                if not para:
                    para_line = i + 1
                para.append(line.strip())
            elif para:
                purpose = (" ".join(para), para_line)
        if line.lstrip().startswith("|") and i + 1 < len(lines) and re.match(r"^\s*\|[\s:|-]+\|\s*$", lines[i + 1]):
            header = [c.lower() for c in _cells(line)]
            name_col = next((k for k, c in enumerate(header) if c in ("name", "topic", "topic name")), None)
            desc_col = next((k for k, c in enumerate(header) if "description" in c), None)
            type_col = next((k for k, c in enumerate(header) if c == "type"), None)
            low = heading.lower()
            role = "input" if ("input" in low or "subscri" in low) else (
                "output" if ("output" in low or "publish" in low) else "")
            i += 2
            while i < len(lines) and lines[i].lstrip().startswith("|"):
                cells = _cells(lines[i])
                if name_col is not None and desc_col is not None and max(name_col, desc_col) < len(cells):
                    kind = _clean(cells[type_col]) if type_col is not None and type_col < len(cells) else ""
                    if "::srv::" not in kind and "/srv/" not in kind:  # services are not topics
                        rows.append(Row(_clean(cells[name_col]), role, kind, _clean(cells[desc_col]), i + 1))
                i += 1
            continue
        i += 1
    if in_purpose and para and purpose is None:
        purpose = (" ".join(para), para_line)
    return purpose, rows


def _norm(name: str) -> str:
    return name.removeprefix("~/").strip("/")


def apply_readme_docs(graph: Graph, packages: dict[str, PackageInfo]) -> int:
    """Attach README purposes to nodes and README rows to topics; returns rows attached."""
    parsed: dict[str, tuple[tuple[str, int] | None, list[Row], str]] = {}
    for name, info in packages.items():
        readme = info.path / "README.md"
        if readme.is_file():
            purpose, rows = parse_readme(readme.read_text(encoding="utf-8", errors="replace"))
            parsed[name] = (purpose, rows, f"{info.rel_path}/README.md")
    attached = 0
    for node in graph.nodes.values():
        entry = parsed.get(node.package)
        if entry is None:
            continue
        purpose, rows, rel = entry
        if purpose and not node.docs:
            node.docs.append(Doc(text=purpose[0], source=f"{rel}:{purpose[1]}", package=node.package,
                                 role="purpose"))
        for e in graph.edges:
            if e.node != node.id:
                continue
            m = _REMAP_FROM.search(e.evidence)
            local = _norm(m.group(1)) if m else None
            want = "input" if e.direction == Direction.subscribe else "output"
            for row in rows:
                if row.role and row.role != want:
                    continue
                if row.name != e.topic and _norm(row.name) != local:
                    continue
                topic = graph.topics[e.topic]
                if any(d.package == node.package and d.text == row.text for d in topic.docs):
                    continue
                topic.docs.append(Doc(text=row.text, source=f"{rel}:{row.line}", package=node.package,
                                      role=want))
                attached += 1
    return attached
