"""CSV writers for the three output artifacts."""

from __future__ import annotations

import csv
import os
from typing import Optional

from .model import Interaction, NodeProfile

# UTF-8 with BOM so handles/display names render correctly when opened directly
# in Excel; Gephi and pandas read it fine too.
_ENCODING = "utf-8-sig"


def write_engagement_table(path: str, fieldnames: list[str], rows: list[dict]) -> None:
    with open(path, "w", newline="", encoding=_ENCODING) as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_edges(path: str, edges: list[Interaction], profiles: dict[str, NodeProfile]) -> None:
    """Interaction-level edge list (Gephi-ready): source, target, type, timestamp."""
    def handle(did: str) -> str:
        p = profiles.get(did)
        return p.handle if p else did

    with open(path, "w", newline="", encoding=_ENCODING) as f:
        writer = csv.writer(f)
        writer.writerow(["source", "target", "type", "timestamp"])
        for e in edges:
            writer.writerow([handle(e.source_did), handle(e.target_did),
                             e.kind.value, e.timestamp.isoformat()])


def write_nodes(path: str, profiles: dict[str, NodeProfile]) -> None:
    with open(path, "w", newline="", encoding=_ENCODING) as f:
        writer = csv.writer(f)
        writer.writerow(["id", "did", "display_name", "followers", "follows", "posts", "is_focal"])
        for p in profiles.values():
            writer.writerow([
                p.handle, p.did, p.display_name,
                _num(p.followers), _num(p.follows), _num(p.posts),
                "true" if p.is_focal else "false",
            ])


def output_dir_for(base: str, handle: str) -> str:
    safe = handle.replace("/", "_").replace("\\", "_")
    path = os.path.join(base, safe)
    os.makedirs(path, exist_ok=True)
    return path


def _num(v: Optional[int]) -> str:
    return "" if v is None else str(v)
