"""Ingest sources. Each module turns one file format into (or annotates) a Graph."""

from .autoware_tree import ingest_autoware_tree

__all__ = ["ingest_autoware_tree"]
