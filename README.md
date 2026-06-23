# bsky-engagement

Export a Bluesky user's **engagement graph** to a spreadsheet and Gephi-ready
CSVs. Given one or more handles, it figures out *who you interact with* and
*who interacts back* — likes, replies, reposts, mentions, and quotes — over a
time window, and writes tables you can open in Excel or import into
[Gephi](https://gephi.org/) to map your posting ecosystem.

It reads **only public data** over the AT Protocol. No login is required.
Nothing is ever written to your account.

## Download

Grab a prebuilt app from the [**Releases**](https://github.com/aaricie/bluesky-engagement/releases/latest)
page — no Python install needed:

- **Windows:** download the `.exe` and double-click it. (Windows SmartScreen may
  warn it's from an unknown publisher — choose *More info → Run anyway*.)
- **macOS:** if a `.app` is attached, download it and right-click → *Open* the
  first time (Gatekeeper warns about unsigned apps). If no Mac build is attached
  yet, [build it from source](#building-standalone-binaries) on a Mac.

Prefer the command line or want the latest unreleased code? See
[Install &amp; run (from source)](#install--run-from-source).

## What you get

For each handle, a folder containing three files:

| File | What it is |
|------|-----------|
| `engagement_by_handle.csv` | One row per person you engage with: per-type interaction counts (out **and** back), recent-window totals, first/last interaction, follow/mutual flags, and their follower/post counts. Open this in a spreadsheet. |
| `edges.csv` | One row per individual interaction: `source, target, type, timestamp`. Drop into Gephi — it builds a directed graph and supports timeline filtering on the timestamps. |
| `nodes.csv` | One row per account in the graph, with profile metrics. The Gephi node table. |

## How it works (and why it's fast)

A like/repost record's subject URI embeds the *target's* DID, so engagement can
be reconstructed by reading **repos** instead of crawling every post:

1. **Outbound** — read your repo once → everyone you engaged with. This alone
   covers "who I posted to / reposted / mentioned" and is the fast default.
2. *(optional, `--top`)* Rank those people and pick the **top N** (or all).
3. **Inbound** — *who engaged with you.*
   - **Logged in as your own account (recommended):** uses the notifications
     feed — one fast indexed read of just the events aimed at you (seconds).
   - **No login (or a third-party handle):** scans each chosen counterparty's
     repo and keeps the records pointing back at you. Correct, but slow for very
     active accounts, since it pages their whole history to find your slice — in
     return, it is **not** limited to ~2 months and can reach back as far as the
     window asks.

The inbound pass is opt-in (`--top`). The fast notifications path only retains
**~2 months** of history, so longer inbound windows there are capped at what's
retained (you'll see a note in the log). To fetch inbound further back, leave
login empty and use the unauthenticated path. Outbound is unaffected either way.

## Install & run (from source)

Requires Python 3.10+.

```bash
pip install -e .          # or: pip install httpx
```

### GUI

```bash
python -m bsky_engagement.gui
```

Enter handles, pick a window and a top-N, choose an output folder, click
**Export Engagement**.

### CLI

```bash
python -m bsky_engagement parisien.cc --window 90d                 # outbound only (fast)
python -m bsky_engagement parisien.cc --window 90d --top 25        # + inbound for top 25
python -m bsky_engagement a.bsky.social b.bsky.social --top all    # + inbound for everyone
```

| Option | Default | Meaning |
|--------|---------|---------|
| `handles` | — | One or more focal handles (positional). |
| `--window` | `90d` | `7d`, `30d`, `60d`, `90d`, `1y`, `all`. |
| `--top` | `off` | Inbound pass (who engaged *back*): `off` (outbound only, fast), an integer `N` (top-N most-engaged), or `all` (every counterparty, heavy). |
| `--out` | `output` | Output dir; one subfolder per handle. |
| `--concurrency` | `6` | Parallel repo reads in the inbound pass. |
| `--auth-handle` / `--auth-app-password` | off | [App-password](https://bsky.app/settings/app-passwords) login (read-only). Also read from `BSKY_HANDLE` / `BSKY_APP_PASSWORD`. **Logging in as the handle you're analyzing switches inbound to the fast notifications path.** Outbound needs no login. |

### A note on runtime

- **Outbound** scales with how active *you* are; the **follow-graph** fetch
  scales with your follower count (both read from your PDS).
- **Inbound** is fast when you're logged in as the account you're analyzing
  (notifications). For third-party handles it falls back to repo scanning,
  which can take minutes per very active counterparty — prefer a smaller
  `--window`/`--top` there.

Tip: to map your own ecosystem with full inbound, log in and use a window
within notification retention (≤ ~2 months), e.g.
`python -m bsky_engagement you.bsky.social --window 30d --top 50 --auth-handle you.bsky.social --auth-app-password xxxx-...`.

## Building standalone binaries

Uses [PyInstaller](https://pyinstaller.org/). It **cannot cross-compile**, so
build on each OS you want to ship for.

```bash
pip install -e ".[build]"
pyinstaller build/bsky-engagement-gui.spec      # GUI -> dist/
```

- **Windows** produces `dist/bsky-engagement.exe`.
- **macOS** produces `dist/bsky-engagement.app`.

For a CLI-only console binary:

```bash
pyinstaller --onefile --name bsky-engagement-cli bsky_engagement/__main__.py
```

## License

MIT.
