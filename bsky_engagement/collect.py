"""Repo readers that turn an account's records into Interaction edges.

All engagement is reconstructed from the *source* account's own repo via
com.atproto.repo.listRecords (newest-first, window-bounded):

  app.bsky.feed.like   -> like edges     (target = subject.uri author)
  app.bsky.feed.repost -> repost edges   (target = subject.uri author)
  app.bsky.feed.post   -> reply edges    (target = reply.parent.uri author)
                          mention edges  (facet #mention -> did)
                          quote edges    (embed record uri author)

`read_author_engagement` works for both passes: pass target_filter=None for the
focal user (keep every counterparty) or {focal_did} for an inbound pass (keep
only edges pointing back at the focal user).
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Callable, Iterator, Optional

from .client import PUBLIC_APPVIEW, Client, XRPCError
from .identity import Identity, post_uri_author_did
from .model import EdgeKind, Interaction, NodeProfile

_MENTION_TYPE = "app.bsky.richtext.facet#mention"
_FRAC_RE = re.compile(r"(\.\d{6})\d+")

# listNotifications reasons we treat as inbound engagement. The *-via-repost
# variants are real engagement with your content (surfaced through a repost),
# so they fold into their base kind. 'follow' (graph, not interaction),
# 'subscribed-post' (post alerts), 'starterpack-joined', etc. are ignored.
_NOTIF_KIND = {
    "like": EdgeKind.LIKE,
    "like-via-repost": EdgeKind.LIKE,
    "repost": EdgeKind.REPOST,
    "repost-via-repost": EdgeKind.REPOST,
    "reply": EdgeKind.REPLY,
    "quote": EdgeKind.QUOTE,
    "mention": EdgeKind.MENTION,
}


def parse_ts(value: Optional[str]) -> Optional[datetime]:
    """Parse an AT Protocol ISO8601 timestamp into an aware UTC datetime."""
    if not value:
        return None
    s = value.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    for candidate in (s, _FRAC_RE.sub(r"\1", s)):
        try:
            dt = datetime.fromisoformat(candidate)
            break
        except ValueError:
            dt = None
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _iter_records(
    client: Client,
    pds: str,
    did: str,
    collection: str,
    since: Optional[datetime],
) -> Iterator[tuple[dict[str, Any], datetime]]:
    """Yield (record_value, createdAt) for a collection, newest-first.

    Stops as soon as a record predates `since` (records are reverse-chron).
    Records without a parseable createdAt are skipped but don't halt paging.
    """
    cursor: Optional[str] = None
    while True:
        data = client.get(
            "com.atproto.repo.listRecords",
            {"repo": did, "collection": collection, "limit": 100, "cursor": cursor},
            base_url=pds,
        )
        records = data.get("records", [])
        for rec in records:
            value = rec.get("value", {})
            ts = parse_ts(value.get("createdAt"))
            if ts is None:
                continue
            if since is not None and ts < since:
                return
            yield value, ts
        cursor = data.get("cursor")
        if not cursor or not records:
            return


def _emit(
    out: list[Interaction],
    source: str,
    target: Optional[str],
    kind: EdgeKind,
    ts: datetime,
    target_filter: Optional[set[str]],
) -> None:
    if not target or target == source:
        return  # drop missing targets and self-engagement
    if target_filter is not None and target not in target_filter:
        return
    out.append(Interaction(source, target, kind, ts))


def _quote_target(embed: dict[str, Any]) -> Optional[str]:
    """Extract the quoted post's author DID from a raw record embed."""
    etype = embed.get("$type", "")
    if etype.startswith("app.bsky.embed.record") and "recordWithMedia" not in etype:
        uri = embed.get("record", {}).get("uri")
    elif "recordWithMedia" in etype:
        uri = embed.get("record", {}).get("record", {}).get("uri")
    else:
        uri = None
    return post_uri_author_did(uri) if uri else None


def read_author_engagement(
    client: Client,
    ident: Identity,
    source_did: str,
    since: Optional[datetime],
    target_filter: Optional[set[str]] = None,
) -> list[Interaction]:
    """All outbound interactions made by `source_did`, optionally filtered."""
    pds = ident.pds_for(source_did)
    out: list[Interaction] = []

    for value, ts in _iter_records(client, pds, source_did, "app.bsky.feed.like", since):
        _emit(out, source_did, post_uri_author_did(value.get("subject", {}).get("uri", "")),
              EdgeKind.LIKE, ts, target_filter)

    for value, ts in _iter_records(client, pds, source_did, "app.bsky.feed.repost", since):
        _emit(out, source_did, post_uri_author_did(value.get("subject", {}).get("uri", "")),
              EdgeKind.REPOST, ts, target_filter)

    for value, ts in _iter_records(client, pds, source_did, "app.bsky.feed.post", since):
        reply = value.get("reply")
        if reply:
            _emit(out, source_did, post_uri_author_did(reply.get("parent", {}).get("uri", "")),
                  EdgeKind.REPLY, ts, target_filter)
        for facet in value.get("facets", []) or []:
            for feature in facet.get("features", []) or []:
                if feature.get("$type") == _MENTION_TYPE:
                    _emit(out, source_did, feature.get("did"), EdgeKind.MENTION, ts, target_filter)
        embed = value.get("embed")
        if embed:
            _emit(out, source_did, _quote_target(embed), EdgeKind.QUOTE, ts, target_filter)

    return out


def read_inbound_notifications(
    client: Client,
    pds: str,
    focal_did: str,
    since: Optional[datetime],
    report: Optional[Callable[[str], None]] = None,
) -> tuple[list[Interaction], Optional[datetime]]:
    """Read who engaged with the *authenticated* focal account, via notifications.

    Far faster than scanning each counterparty's repo (one indexed feed of just
    the events aimed at you), but limited to whatever notification history the
    AppView retains (~weeks). Returns (edges, oldest_reachable_ts).
    """
    out: list[Interaction] = []
    cursor: Optional[str] = None
    pages = 0
    oldest: Optional[datetime] = None
    exhausted = False

    while True:
        data = client.get(
            "app.bsky.notification.listNotifications",
            {"limit": 100, "cursor": cursor},
            base_url=pds,
            authed=True,
        )
        notes = data.get("notifications", [])
        if not notes:
            exhausted = True
            break
        stop = False
        for n in notes:
            ts = parse_ts((n.get("record") or {}).get("createdAt")) or parse_ts(n.get("indexedAt"))
            if ts is None:
                continue
            if oldest is None or ts < oldest:
                oldest = ts
            if since is not None and ts < since:
                stop = True  # notifications are newest-first
                break
            kind = _NOTIF_KIND.get(n.get("reason"))
            if kind is None:
                continue
            author = (n.get("author") or {}).get("did")
            if author and author != focal_did:
                out.append(Interaction(author, focal_did, kind, ts))
        pages += 1
        cursor = data.get("cursor")
        if report and pages % 10 == 0:
            report(f"inbound notifications: {len(out)} kept ({pages} pages)")
        if stop:
            break
        if not cursor:
            exhausted = True
            break

    # Warn if retention cut us short of the requested window.
    if report and exhausted and since is not None and oldest is not None and oldest > since:
        report(f"note: notification history only reaches {oldest.date()}; "
               f"inbound before that is unavailable (AppView retention)")
    return out, oldest


def fetch_profile(client: Client, did: str, *, is_focal: bool = False) -> NodeProfile:
    """Fetch profile metrics; degrade gracefully for deleted/blocked accounts."""
    try:
        p = client.get("app.bsky.actor.getProfile", {"actor": did}, base_url=PUBLIC_APPVIEW)
    except XRPCError:
        return NodeProfile(did=did, handle=did, is_focal=is_focal)
    return NodeProfile(
        did=did,
        handle=p.get("handle", did),
        display_name=p.get("displayName", "") or "",
        followers=p.get("followersCount"),
        follows=p.get("followsCount"),
        posts=p.get("postsCount"),
        is_focal=is_focal,
    )


def _profile_from_view(p: dict[str, Any], *, is_focal: bool = False) -> NodeProfile:
    return NodeProfile(
        did=p.get("did", ""),
        handle=p.get("handle", p.get("did", "")),
        display_name=p.get("displayName", "") or "",
        followers=p.get("followersCount"),
        follows=p.get("followsCount"),
        posts=p.get("postsCount"),
        is_focal=is_focal,
    )


def fetch_profiles(
    client: Client, dids: list[str], focal_did: Optional[str] = None
) -> dict[str, NodeProfile]:
    """Batch-fetch profile metrics (getProfiles, up to 25 actors per call)."""
    out: dict[str, NodeProfile] = {}
    for i in range(0, len(dids), 25):
        chunk = dids[i : i + 25]
        try:
            data = client.get("app.bsky.actor.getProfiles", {"actors": chunk},
                              base_url=PUBLIC_APPVIEW)
        except XRPCError:
            data = {"profiles": []}
        for p in data.get("profiles", []):
            out[p["did"]] = _profile_from_view(p, is_focal=(p.get("did") == focal_did))
    # Fill in any DIDs the batch endpoint dropped (deleted/blocked accounts).
    for did in dids:
        out.setdefault(did, NodeProfile(did=did, handle=did, is_focal=(did == focal_did)))
    return out


def _collect_actor_dids(client: Client, method: str, did: str, key: str) -> set[str]:
    dids: set[str] = set()
    cursor: Optional[str] = None
    while True:
        data = client.get(method, {"actor": did, "limit": 100, "cursor": cursor},
                          base_url=PUBLIC_APPVIEW)
        for actor in data.get(key, []):
            if actor.get("did"):
                dids.add(actor["did"])
        cursor = data.get("cursor")
        if not cursor:
            return dids


def fetch_follow_sets(client: Client, did: str) -> tuple[set[str], set[str]]:
    """Return (dids the focal follows, dids that follow the focal)."""
    follows = _collect_actor_dids(client, "app.bsky.graph.getFollows", did, "follows")
    followers = _collect_actor_dids(client, "app.bsky.graph.getFollowers", did, "followers")
    return follows, followers
