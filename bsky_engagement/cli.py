"""Command-line entry point."""

from __future__ import annotations

import argparse
import os
import sys
import time

from .model import InboundMode, RunConfig, parse_window
from .pipeline import run


def parse_top(value: str) -> tuple[InboundMode, int]:
    """Parse --top into (inbound_mode, n). Accepts 'off', 'all', or an integer."""
    v = value.strip().lower()
    if v == "off":
        return InboundMode.OFF, 25
    if v == "all":
        return InboundMode.ALL, 25
    if v.isdigit() and int(v) > 0:
        return InboundMode.TOP, int(v)
    raise ValueError(f"invalid --top {value!r}; expected 'off', 'all', or a positive integer")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="bsky-engagement",
        description="Export a Bluesky user's engagement to spreadsheet + Gephi CSVs.",
    )
    p.add_argument(
        "handles",
        nargs="+",
        help="One or more focal handles, e.g. wikisteff.bsky.social",
    )
    p.add_argument(
        "--window",
        default="90d",
        help="Time window to fetch: 7d, 30d, 60d, 90d, 1y, all (default: 90d).",
    )
    p.add_argument(
        "--top",
        default="off",
        metavar="off|all|N",
        help="Inbound pass (who engaged back): 'off' (outbound only, fast; "
             "default), an integer N (top-N most-engaged), or 'all' (every "
             "counterparty, heavy).",
    )
    p.add_argument(
        "--out",
        default="output",
        help="Output directory; one subfolder per handle (default: ./output).",
    )
    p.add_argument(
        "--concurrency",
        type=int,
        default=6,
        help="Parallel repo reads in the inbound pass (default: 6).",
    )
    p.add_argument(
        "--auth-handle",
        default=os.environ.get("BSKY_HANDLE"),
        help="Optional: handle for app-password login (or set BSKY_HANDLE).",
    )
    p.add_argument(
        "--auth-app-password",
        default=os.environ.get("BSKY_APP_PASSWORD"),
        help="Optional: app password (or set BSKY_APP_PASSWORD). Read-only use.",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        window = parse_window(args.window)
        inbound_mode, top = parse_top(args.top)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    config = RunConfig(
        handles=args.handles,
        inbound_mode=inbound_mode,
        top=top,
        window=window,
        out_dir=args.out,
        concurrency=max(1, args.concurrency),
        auth_handle=args.auth_handle,
        auth_app_password=args.auth_app_password,
    )

    def progress(msg: str, frac: float) -> None:
        print(f"[{frac * 100:3.0f}%] {msg}", flush=True)

    start = time.monotonic()
    try:
        results = run(config, progress)
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130
    except Exception as e:  # noqa: BLE001
        print(f"error: {e}", file=sys.stderr)
        return 1

    elapsed = time.monotonic() - start
    print(f"\nDone in {elapsed:.0f}s:")
    for r in results:
        print(f"  {r.handle}: {r.counterparties} counterparties, "
              f"{r.edges} edges -> {r.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
