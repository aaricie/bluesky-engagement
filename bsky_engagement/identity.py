"""Handle/DID/PDS resolution with simple in-memory caching.

- handle -> DID via com.atproto.identity.resolveHandle (public AppView).
- DID  -> PDS service endpoint via the DID document (PLC directory for
  did:plc, the well-known did.json for did:web).
"""

from __future__ import annotations

from typing import Optional

from .client import PLC_DIRECTORY, PUBLIC_APPVIEW, Client, XRPCError


class Identity:
    def __init__(self, client: Client):
        self._c = client
        self._handle_to_did: dict[str, str] = {}
        self._did_to_pds: dict[str, str] = {}

    def resolve_handle(self, handle: str) -> str:
        handle = handle.strip().lstrip("@").lower()
        if handle in self._handle_to_did:
            return self._handle_to_did[handle]
        data = self._c.get(
            "com.atproto.identity.resolveHandle",
            {"handle": handle},
            base_url=PUBLIC_APPVIEW,
        )
        did = data["did"]
        self._handle_to_did[handle] = did
        return did

    def pds_for(self, did: str) -> str:
        if did in self._did_to_pds:
            return self._did_to_pds[did]
        endpoint = self._lookup_pds(did)
        self._did_to_pds[did] = endpoint
        return endpoint

    def _lookup_pds(self, did: str) -> str:
        doc = self._fetch_did_doc(did)
        for svc in doc.get("service", []) or []:
            if svc.get("id", "").endswith("#atproto_pds") or (
                svc.get("type") == "AtprotoPersonalDataServer"
            ):
                endpoint = svc.get("serviceEndpoint")
                if endpoint:
                    return endpoint.rstrip("/")
        raise XRPCError(0, "resolvePDS", f"no PDS service endpoint in DID doc for {did}")

    def _fetch_did_doc(self, did: str) -> dict:
        if did.startswith("did:plc:"):
            resp = self._c._http.get(f"{PLC_DIRECTORY}/{did}")
        elif did.startswith("did:web:"):
            host = did[len("did:web:") :].replace(":", "/")
            resp = self._c._http.get(f"https://{host}/.well-known/did.json")
        else:
            raise XRPCError(0, "resolveDID", f"unsupported DID method: {did}")
        if resp.status_code != 200:
            raise XRPCError(resp.status_code, "resolveDID", f"could not fetch DID doc for {did}")
        return resp.json()


def post_uri_author_did(at_uri: str) -> Optional[str]:
    """Extract the author DID from an at:// URI.

    e.g. at://did:plc:abc/app.bsky.feed.post/3k... -> did:plc:abc
    Returns None if the URI isn't shaped as expected.
    """
    if not at_uri.startswith("at://"):
        return None
    rest = at_uri[len("at://") :]
    authority = rest.split("/", 1)[0]
    return authority if authority.startswith("did:") else None
