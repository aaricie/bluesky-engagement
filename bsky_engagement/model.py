"""Core data types shared across the pipeline.

Edges are stored directionally as (source_did, target_did, kind, timestamp).
Direction relative to the focal user (outbound vs inbound) is derived at
aggregation time by comparing each end against the focal DID, so the raw edge
list stays a plain directed graph that drops straight into Gephi.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional


class EdgeKind(str, Enum):
    LIKE = "like"
    REPLY = "reply"
    REPOST = "repost"
    MENTION = "mention"
    QUOTE = "quote"


# Sub-window breakdowns precomputed into the wide spreadsheet, in days.
SUBWINDOWS_DAYS = (7, 30, 90)


@dataclass(frozen=True, slots=True)
class Interaction:
    """One directed engagement event between two accounts (keyed on DIDs)."""

    source_did: str
    target_did: str
    kind: EdgeKind
    # Timezone-aware UTC datetime of the underlying record's createdAt.
    timestamp: datetime


@dataclass(slots=True)
class NodeProfile:
    """Profile metrics for one account (a graph node)."""

    did: str
    handle: str = ""
    display_name: str = ""
    followers: Optional[int] = None
    follows: Optional[int] = None
    posts: Optional[int] = None
    is_focal: bool = False


class InboundMode(str, Enum):
    OFF = "off"    # outbound only; no inbound pass (fast default)
    TOP = "top"    # inbound for the top-N most-engaged counterparties
    ALL = "all"    # inbound for every counterparty (heavy)


@dataclass(slots=True)
class RunConfig:
    """Everything one invocation of the pipeline needs."""

    handles: list[str]
    # The inbound pass (who engaged back) is the heavy part, so it's opt-in.
    inbound_mode: InboundMode = InboundMode.OFF
    top: int = 25  # only used when inbound_mode == TOP
    window: Optional[timedelta] = None  # None == "all"
    out_dir: str = "output"
    concurrency: int = 6
    # Optional app-password auth (kept in reserve; public reads are the default).
    auth_handle: Optional[str] = None
    auth_app_password: Optional[str] = None

    @property
    def authenticated(self) -> bool:
        return bool(self.auth_handle and self.auth_app_password)


_WINDOW_RE = re.compile(r"^\s*(\d+)\s*([dwmy])\s*$", re.IGNORECASE)
_UNIT_DAYS = {"d": 1, "w": 7, "m": 30, "y": 365}


def parse_window(value: str) -> Optional[timedelta]:
    """Parse a window string like '7d', '30d', '90d', '1y', or 'all'.

    Returns None for 'all' (no time bound). Raises ValueError on bad input.
    """
    v = value.strip().lower()
    if v == "all":
        return None
    m = _WINDOW_RE.match(v)
    if not m:
        raise ValueError(
            f"invalid window {value!r}; expected e.g. 7d, 30d, 90d, 12w, 1y, or all"
        )
    n, unit = int(m.group(1)), m.group(2)
    return timedelta(days=n * _UNIT_DAYS[unit])
