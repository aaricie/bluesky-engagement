"""Thin synchronous XRPC client over httpx.

Two host roles:
- The public AppView (``public.api.bsky.app``) serves the hydrated app.bsky.*
  views (profiles, author feeds, follow graph) without auth.
- Each account's PDS serves com.atproto.repo.listRecords for that account's
  repo. PDS hosts vary per account (PDS distribution), so the caller resolves
  the right base URL per request.

Handles 429s with Retry-After + exponential backoff. Optional app-password
auth is supported but off by default; all data we need is public.
"""

from __future__ import annotations

import time
from typing import Any, Optional

import httpx

PUBLIC_APPVIEW = "https://public.api.bsky.app"
DEFAULT_ENTRYWAY = "https://bsky.social"
PLC_DIRECTORY = "https://plc.directory"

_USER_AGENT = "bsky-engagement/0.1 (+https://github.com/)"


class XRPCError(RuntimeError):
    def __init__(self, status: int, method: str, message: str):
        super().__init__(f"{method} -> HTTP {status}: {message}")
        self.status = status
        self.method = method


class Client:
    def __init__(
        self,
        *,
        timeout: float = 30.0,
        max_retries: int = 5,
        access_token: Optional[str] = None,
    ):
        self._http = httpx.Client(
            timeout=timeout,
            headers={"User-Agent": _USER_AGENT},
            follow_redirects=True,
        )
        self._max_retries = max_retries
        self._access_token = access_token
        # Populated by login(): the authenticated account's identity.
        self.session_did: Optional[str] = None
        self.session_handle: Optional[str] = None

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "Client":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- core request ---------------------------------------------------------

    def get(
        self,
        method: str,
        params: Optional[dict[str, Any]] = None,
        *,
        base_url: str = PUBLIC_APPVIEW,
        authed: bool = False,
    ) -> dict[str, Any]:
        """Call an XRPC query (GET) and return the parsed JSON body."""
        url = f"{base_url.rstrip('/')}/xrpc/{method}"
        headers = {}
        if authed and self._access_token:
            headers["Authorization"] = f"Bearer {self._access_token}"

        attempt = 0
        while True:
            resp = self._http.get(url, params=_clean(params), headers=headers)
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code == 429 and attempt < self._max_retries:
                self._sleep_for_rate_limit(resp, attempt)
                attempt += 1
                continue
            if resp.status_code in (502, 503, 504) and attempt < self._max_retries:
                time.sleep(min(2**attempt, 30))
                attempt += 1
                continue
            raise XRPCError(resp.status_code, method, _err_text(resp))

    def post(
        self,
        method: str,
        json_body: dict[str, Any],
        *,
        base_url: str,
    ) -> dict[str, Any]:
        url = f"{base_url.rstrip('/')}/xrpc/{method}"
        resp = self._http.post(url, json=json_body)
        if resp.status_code != 200:
            raise XRPCError(resp.status_code, method, _err_text(resp))
        return resp.json()

    # -- auth (optional) ------------------------------------------------------

    def login(self, identifier: str, app_password: str, base_url: str = DEFAULT_ENTRYWAY) -> None:
        data = self.post(
            "com.atproto.server.createSession",
            {"identifier": identifier, "password": app_password},
            base_url=base_url,
        )
        self._access_token = data["accessJwt"]
        self.session_did = data.get("did")
        self.session_handle = data.get("handle")

    # -- helpers --------------------------------------------------------------

    def _sleep_for_rate_limit(self, resp: httpx.Response, attempt: int) -> None:
        retry_after = resp.headers.get("retry-after")
        if retry_after and retry_after.isdigit():
            time.sleep(int(retry_after))
        else:
            time.sleep(min(2**attempt, 60))


def _clean(params: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """Drop None-valued params so we don't send empty query keys."""
    if not params:
        return params
    return {k: v for k, v in params.items() if v is not None}


def _err_text(resp: httpx.Response) -> str:
    try:
        body = resp.json()
        return body.get("message") or body.get("error") or resp.text[:200]
    except Exception:
        return resp.text[:200]
