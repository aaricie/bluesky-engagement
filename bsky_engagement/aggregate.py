"""Turn the raw interaction edge list into the wide per-counterparty table.

Direction is derived relative to the focal user: an edge whose source is the
focal user is *outbound* (focal engaged with the counterparty); an edge whose
target is the focal user is *inbound* (counterparty engaged with focal).
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Optional

from .model import EdgeKind, Interaction, NodeProfile

_KINDS = [k.value for k in EdgeKind]
# Column-name plurals (naive +"s" would give "replys").
_PLURAL = {
    "like": "likes",
    "reply": "replies",
    "repost": "reposts",
    "mention": "mentions",
    "quote": "quotes",
}


def rank_counterparties(outbound: list[Interaction], focal_did: str) -> list[str]:
    """DIDs the focal engaged with, most-engaged first."""
    counts: Counter[str] = Counter()
    for e in outbound:
        if e.source_did == focal_did:
            counts[e.target_did] += 1
    return [did for did, _ in counts.most_common()]


def rank_by_total(edges: list[Interaction], focal_did: str) -> list[str]:
    """All counterparties (either direction), most total interactions first."""
    counts: Counter[str] = Counter()
    for e in edges:
        cp = _counterparty(e, focal_did)
        if cp is not None:
            counts[cp] += 1
    return [did for did, _ in counts.most_common()]


def _counterparty(edge: Interaction, focal_did: str) -> Optional[str]:
    if edge.source_did == focal_did:
        return edge.target_did
    if edge.target_did == focal_did:
        return edge.source_did
    return None


def build_table(
    focal_did: str,
    edges: list[Interaction],
    follows: set[str],
    followers: set[str],
    profiles: dict[str, NodeProfile],
    subwindow_days: list[int],
    now: Optional[datetime] = None,
) -> tuple[list[str], list[dict]]:
    """Return (fieldnames, rows) for engagement_by_handle.csv, sorted by total."""
    now = now or datetime.now(timezone.utc)
    cutoffs = {d: now - timedelta(days=d) for d in subwindow_days}

    # Gather each counterparty's edges split by direction.
    per_cp: dict[str, dict] = defaultdict(
        lambda: {
            "out": Counter(),
            "in": Counter(),
            "out_win": defaultdict(int),
            "in_win": defaultdict(int),
            "first": None,
            "last": None,
        }
    )
    for e in edges:
        cp = _counterparty(e, focal_did)
        if cp is None:
            continue
        direction = "out" if e.source_did == focal_did else "in"
        agg = per_cp[cp]
        agg[direction][e.kind.value] += 1
        for d, cutoff in cutoffs.items():
            if e.timestamp >= cutoff:
                agg[f"{direction}_win"][d] += 1
        if agg["first"] is None or e.timestamp < agg["first"]:
            agg["first"] = e.timestamp
        if agg["last"] is None or e.timestamp > agg["last"]:
            agg["last"] = e.timestamp

    fieldnames = _build_fieldnames(subwindow_days)
    rows: list[dict] = []
    for cp, agg in per_cp.items():
        prof = profiles.get(cp) or NodeProfile(did=cp, handle=cp)
        total_out = sum(agg["out"].values())
        total_in = sum(agg["in"].values())
        row = {
            "handle": prof.handle,
            "display_name": prof.display_name,
            "total_interactions": total_out + total_in,
            "total_out": total_out,
            "total_in": total_in,
        }
        for k in _KINDS:
            row[f"{_PLURAL[k]}_out"] = agg["out"].get(k, 0)
        for k in _KINDS:
            row[f"{_PLURAL[k]}_in"] = agg["in"].get(k, 0)
        for d in subwindow_days:
            row[f"total_out_{d}d"] = agg["out_win"].get(d, 0)
            row[f"total_in_{d}d"] = agg["in_win"].get(d, 0)
        row["first_interaction"] = _iso(agg["first"])
        row["last_interaction"] = _iso(agg["last"])
        row["i_follow_them"] = cp in follows
        row["they_follow_me"] = cp in followers
        row["mutual"] = cp in follows and cp in followers
        row["their_followers"] = prof.followers if prof.followers is not None else ""
        row["their_follows"] = prof.follows if prof.follows is not None else ""
        row["their_posts"] = prof.posts if prof.posts is not None else ""
        row["did"] = cp
        rows.append(row)

    rows.sort(key=lambda r: r["total_interactions"], reverse=True)
    return fieldnames, rows


def _build_fieldnames(subwindow_days: list[int]) -> list[str]:
    names = ["handle", "display_name", "total_interactions", "total_out", "total_in"]
    names += [f"{_PLURAL[k]}_out" for k in _KINDS]
    names += [f"{_PLURAL[k]}_in" for k in _KINDS]
    for d in subwindow_days:
        names += [f"total_out_{d}d", f"total_in_{d}d"]
    names += [
        "first_interaction", "last_interaction",
        "i_follow_them", "they_follow_me", "mutual",
        "their_followers", "their_follows", "their_posts", "did",
    ]
    return names


def _iso(dt: Optional[datetime]) -> str:
    return dt.isoformat() if dt else ""
