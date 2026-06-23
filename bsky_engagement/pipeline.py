"""Orchestrates a full engagement export for one or more focal handles.

For each handle:
  1. resolve handle -> DID
  2. focal pass: read the focal repo -> all outbound interactions
  3. choose counterparties to scope the report + (optionally) the inbound pass
  4. inbound pass (concurrent, opt-in): read each chosen repo filtered to the
     focal -> inbound interactions
  5. fetch profiles (batched) + the focal's follow/follower sets
  6. aggregate -> wide table; write the three CSVs

Progress is reported through a callback (message, fraction) where fraction is
overall 0..1 across all handles, so the CLI/GUI can drive a real progress bar.
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Optional

from .aggregate import build_table, rank_by_total, rank_counterparties
from .client import Client
from .collect import (
    fetch_follow_sets,
    fetch_profiles,
    read_author_engagement,
    read_inbound_notifications,
)
from .identity import Identity
from .model import SUBWINDOWS_DAYS, InboundMode, Interaction, RunConfig
from .output import (
    output_dir_for,
    write_edges,
    write_engagement_table,
    write_nodes,
)

# progress(message, overall_fraction in [0, 1])
ProgressFn = Callable[[str, float], None]


@dataclass
class HandleResult:
    handle: str
    did: str
    out_dir: str
    counterparties: int
    edges: int


def _noop(_msg: str, _frac: float) -> None:
    pass


def _applicable_subwindows(config: RunConfig) -> list[int]:
    """Only emit sub-window columns that fit inside the fetched window."""
    if config.window is None:
        return list(SUBWINDOWS_DAYS)
    fetched_days = config.window.days
    return [d for d in SUBWINDOWS_DAYS if d <= fetched_days] or [fetched_days]


def run(config: RunConfig, progress: Optional[ProgressFn] = None) -> list[HandleResult]:
    progress = progress or _noop
    os.makedirs(config.out_dir, exist_ok=True)

    n = max(1, len(config.handles))
    results: list[HandleResult] = []
    with Client() as client:
        if config.authenticated:
            progress("Authenticating with app password...", 0.0)
            client.login(config.auth_handle, config.auth_app_password)
        ident = Identity(client)
        for i, handle in enumerate(config.handles):
            # Scope each handle's local 0..1 progress into its slice of the bar.
            def report(msg: str, lf: float, _i: int = i) -> None:
                progress(msg, (_i + min(max(lf, 0.0), 1.0)) / n)

            results.append(_run_one(client, ident, handle, config, report))
    progress("Done.", 1.0)
    return results


def _run_one(
    client: Client,
    ident: Identity,
    handle: str,
    config: RunConfig,
    report: Callable[[str, float], None],
) -> HandleResult:
    now = datetime.now(timezone.utc)
    since = now - config.window if config.window else None
    inbound_on = config.inbound_mode != InboundMode.OFF

    report(f"[{handle}] resolving identity...", 0.02)
    focal_did = ident.resolve_handle(handle)

    report(f"[{handle}] reading your repo (likes, reposts, posts)...", 0.05)
    outbound = read_author_engagement(client, ident, focal_did, since)

    # Scope = whom the report (rows/nodes/edges) covers: TOP keeps the top-N,
    # OFF/ALL keep everyone. The inbound pass runs only when not OFF.
    def pick(ordered: list[str]) -> list[str]:
        return ordered if config.inbound_mode != InboundMode.TOP else ordered[: config.top]

    # Fast inbound path: notifications, available only when authenticated *as*
    # the focal account. Otherwise fall back to scanning counterparty repos.
    use_notifs = inbound_on and client.session_did == focal_did
    inbound: list[Interaction] = []

    if not inbound_on:
        report_dids = pick(rank_counterparties(outbound, focal_did))
    elif use_notifs:
        report(f"[{handle}] reading inbound via notifications (fast path)...", 0.45)
        inbound, _oldest = read_inbound_notifications(
            client, ident.pds_for(focal_did), focal_did, since,
            report=lambda m: report(f"[{handle}] {m}", 0.55),
        )
        report_dids = pick(rank_by_total(outbound + inbound, focal_did))
    else:
        report_dids = pick(rank_counterparties(outbound, focal_did))
        report(f"[{handle}] inbound pass over {len(report_dids)} counterparties...", 0.14)
        inbound = _inbound_pass(
            client, ident, report_dids, focal_did, since, config, report, handle,
            base=0.14, span=0.66,
        )

    report_set = set(report_dids)
    report(
        f"[{handle}] {len(outbound)} outbound, {len(inbound)} inbound; "
        f"scope: {len(report_dids)} counterparties",
        0.78 if inbound_on else 0.45,
    )

    edges: list[Interaction] = [e for e in outbound if e.target_did in report_set]
    edges += [e for e in inbound if e.source_did in report_set]

    report(f"[{handle}] fetching profiles + follow graph...", 0.82 if inbound_on else 0.55)
    profiles = fetch_profiles(client, [focal_did] + report_dids, focal_did=focal_did)
    report(f"[{handle}] fetching follow graph...", 0.90 if inbound_on else 0.70)
    follows, followers = fetch_follow_sets(client, focal_did)

    subwindows = _applicable_subwindows(config)
    fieldnames, rows = build_table(
        focal_did, edges, follows, followers, profiles, subwindows, now=now
    )

    out_dir = output_dir_for(config.out_dir, profiles.get(focal_did).handle or handle)
    write_engagement_table(os.path.join(out_dir, "engagement_by_handle.csv"), fieldnames, rows)
    write_edges(os.path.join(out_dir, "edges.csv"), edges, profiles)
    write_nodes(os.path.join(out_dir, "nodes.csv"), profiles)
    report(f"[{handle}] wrote 3 CSVs to {out_dir}", 1.0)

    return HandleResult(
        handle=profiles.get(focal_did).handle or handle,
        did=focal_did,
        out_dir=out_dir,
        counterparties=len(rows),
        edges=len(edges),
    )


def _inbound_pass(
    client: Client,
    ident: Identity,
    targets: list[str],
    focal_did: str,
    since,
    config: RunConfig,
    report: Callable[[str, float], None],
    handle: str,
    *,
    base: float,
    span: float,
) -> list[Interaction]:
    focal_filter = {focal_did}
    inbound: list[Interaction] = []
    total = len(targets)
    done = 0

    def work(did: str) -> list[Interaction]:
        try:
            return read_author_engagement(client, ident, did, since, target_filter=focal_filter)
        except Exception:  # noqa: BLE001 - one bad repo shouldn't kill the run
            return []

    with ThreadPoolExecutor(max_workers=config.concurrency) as pool:
        futures = {pool.submit(work, did): did for did in targets}
        for fut in as_completed(futures):
            inbound.extend(fut.result())
            done += 1
            report(f"[{handle}] inbound {done}/{total}", base + span * (done / total))
    return inbound
