"""OAuth 2.1 for MCP (PLAN §9 phase 2; MCP authorization, revision 2026-07-28): any MCP client signs in with
the member's Google login instead of taking a pasted token.

A client is a client ID metadata document (an HTTPS URL as client_id, fetched under SSRF guards) or registers
itself (RFC 7591). Codes are single use with S256 PKCE. Access tokens last an hour; refresh tokens rotate, and
reusing one revokes the grant. Every code, token and client secret is stored as its SHA-256 only.
"""

import base64
import hashlib
import hmac
import http.client
import ipaddress
import json
import re
import secrets
import socket
import ssl
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from tijori.db import MemberContext, set_member_context
from tijori.models import McpToken, OAuthClient, OAuthCode, OAuthRequest, OAuthToken
from tijori.services.common import audit, sha256_hex

READ_SCOPE, WRITE_SCOPE = "tijori:read", "tijori:write"
SCOPES = (READ_SCOPE, WRITE_SCOPE)
ACCESS_TTL = timedelta(hours=1)
REFRESH_TTL = timedelta(days=30)  # from the last refresh: a connection used monthly stays signed in
CODE_TTL = timedelta(minutes=5)
REQUEST_TTL = timedelta(minutes=10)
MAX_PENDING = 1000  # bounds the unauthenticated writes /oauth/authorize can cause
MAX_UNUSED_CLIENTS = 500  # same, for /oauth/register
MAX_GRANTS = 20  # live connected apps per member
AUTH_METHODS = ("none", "client_secret_post", "client_secret_basic")
LOOPBACK = frozenset({"localhost", "127.0.0.1", "::1"})
METADATA_MAX_BYTES = 16 * 1024
METADATA_TIMEOUT_S = 5
ACTOR = "mcp-oauth"


class OAuthError(Exception):
    """An RFC 6749 error: `code` goes to the client as `error`, `description` is safe to show. `keep`: commit
    the transaction's writes before answering (a spent code, an ended grant)."""

    def __init__(self, code: str, description: str, status: int = 400, keep: bool = False) -> None:
        super().__init__(description)
        self.code, self.description, self.status, self.keep = code, description, status, keep


def issuer(public_url: str) -> str:
    return public_url.rstrip("/")


def resource(public_url: str) -> str:
    return issuer(public_url) + "/mcp"


def resource_ok(public_url: str, value: str | None) -> bool:
    """RFC 8707: the token is for this MCP server. Scheme and host compare case-insensitively; the bare origin
    also names it, since /mcp is the only resource here."""
    if value is None:
        return True
    p = urlsplit(value.strip())
    if p.query or p.fragment:
        return False
    got = f"{p.scheme.lower()}://{p.netloc.lower()}{p.path.rstrip('/')}"
    return got in (resource(public_url).lower(), issuer(public_url).lower())


def granted_scope(requested: str | None) -> str:
    """Known scopes only; asking for none of them (or for offline_access alone) means both."""
    asked = [s for s in (requested or "").split() if s in SCOPES]
    return " ".join(s for s in SCOPES if s in asked) or " ".join(SCOPES)


def can_write(scope: str | None) -> bool:
    return scope is None or WRITE_SCOPE in scope.split()


def check_redirect_uri(uri: Any) -> str:
    """https, http on loopback (RFC 8252 §7.3), or a reverse-DNS private-use scheme (RFC 8252 §7.1)."""
    if not isinstance(uri, str) or not 0 < len(uri) <= 2000:
        raise OAuthError("invalid_redirect_uri", "redirect_uris must be URLs")
    p = urlsplit(uri)
    scheme = p.scheme.lower()
    if p.fragment:
        raise OAuthError("invalid_redirect_uri", "a redirect URI must not have a fragment")
    if scheme == "https" and p.hostname:
        return uri
    if scheme == "http" and p.hostname in LOOPBACK:
        return uri
    if "." in scheme and re.fullmatch(r"[a-z][a-z0-9+.\-]*", scheme):
        return uri
    raise OAuthError("invalid_redirect_uri", "redirect URIs must be https, http on localhost, or an app's own scheme")


def redirect_matches(registered: list[str], given: str) -> bool:
    """Exact match, except that a loopback redirect may use any port (RFC 8252 §7.3)."""
    if given in registered:
        return True
    g = urlsplit(given)
    if g.scheme != "http" or g.hostname not in LOOPBACK:
        return False
    return any((r.scheme, r.hostname, r.path, r.query) == ("http", g.hostname, g.path, g.query)
               for r in map(urlsplit, registered))


def pkce_ok(verifier: str | None, challenge: str) -> bool:
    if not verifier or not re.fullmatch(r"[A-Za-z0-9\-._~]{43,128}", verifier):
        return False
    digest = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return hmac.compare_digest(digest, challenge)


def _clean(text: Any, limit: int) -> str:
    return re.sub(r"[\x00-\x1f\x7f]", "", str(text or "")).strip()[:limit]


# --- clients --------------------------------------------------------------------------------------------

def register(s: Session, meta: Any) -> dict[str, Any]:
    """RFC 7591. An omitted token_endpoint_auth_method means client_secret_basic, as the RFC says."""
    if not isinstance(meta, dict):
        raise OAuthError("invalid_client_metadata", "the body must be a JSON object")
    uris = meta.get("redirect_uris")
    if not isinstance(uris, list) or not 0 < len(uris) <= 10:
        raise OAuthError("invalid_redirect_uri", "redirect_uris must list 1 to 10 URLs")
    uris = [check_redirect_uri(u) for u in uris]
    grants = meta.get("grant_types") or ["authorization_code"]
    if not isinstance(grants, list) or not set(grants) <= {"authorization_code", "refresh_token"}:
        raise OAuthError("invalid_client_metadata", "grant_types may be authorization_code and refresh_token")
    if (meta.get("response_types") or ["code"]) != ["code"]:
        raise OAuthError("invalid_client_metadata", "response_types must be [\"code\"]")
    method = meta.get("token_endpoint_auth_method") or "client_secret_basic"
    if method not in AUTH_METHODS:
        raise OAuthError("invalid_client_metadata", f"token_endpoint_auth_method must be one of {', '.join(AUTH_METHODS)}")
    now = datetime.now(UTC)
    s.execute(delete(OAuthClient).where(OAuthClient.kind == "registered", OAuthClient.last_used_at.is_(None),
                                        OAuthClient.created_at < now - timedelta(days=7)))
    unused = s.scalar(select(func.count()).select_from(OAuthClient).where(OAuthClient.last_used_at.is_(None))) or 0
    if unused >= MAX_UNUSED_CLIENTS:
        raise OAuthError("temporarily_unavailable", "too many apps registered recently; try again later", 503)
    client_id = "tjc_" + secrets.token_urlsafe(24)
    secret = "tjs_" + secrets.token_urlsafe(32) if method != "none" else None
    name = _clean(meta.get("client_name"), 120) or "Unnamed app"
    s.add(OAuthClient(client_id=client_id, kind="registered", name=name, redirect_uris=uris, auth_method=method,
                      secret_hash=sha256_hex(secret) if secret else None))
    out: dict[str, Any] = {"client_id": client_id, "client_id_issued_at": int(now.timestamp()), "client_name": name,
                           "redirect_uris": uris, "grant_types": grants, "response_types": ["code"],
                           "token_endpoint_auth_method": method}
    if secret:
        out |= {"client_secret": secret, "client_secret_expires_at": 0}
    return out


def is_metadata_client(client_id: str) -> bool:
    return client_id.startswith("https://")


def client_for(s: Session, client_id: str, *, fetch: bool) -> OAuthClient | None:
    """The client, fetching (or refreshing) a metadata document when `fetch`; only a signed-in member's
    consent page fetches, so no stranger can make Tijori call out."""
    row = s.get(OAuthClient, client_id)
    if not is_metadata_client(client_id) or not fetch:
        return row
    now = datetime.now(UTC)
    if row is not None and row.expires_at and row.expires_at > now:
        return row
    doc, ttl = fetch_client_metadata(client_id)
    if row is None:
        row = OAuthClient(client_id=client_id, kind="metadata", auth_method="none", redirect_uris=[], name="")
        s.add(row)
    row.name, row.redirect_uris, row.expires_at = doc["client_name"], doc["redirect_uris"], now + ttl
    return row


class _PinnedHTTPS(http.client.HTTPSConnection):
    """Connects to the address that passed the SSRF check, not a second DNS answer (DNS rebinding)."""

    def __init__(self, host: str, ip: str, timeout: float) -> None:
        super().__init__(host, 443, timeout=timeout, context=ssl.create_default_context())
        self._ip = ip

    def connect(self) -> None:
        sock = socket.create_connection((self._ip, 443), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)  # type: ignore[attr-defined]


def fetch_client_metadata(client_id: str) -> tuple[dict[str, Any], timedelta]:
    """(document, cache lifetime). https on 443 to a public address only, no redirects, 16 KiB, 5 s."""
    p = urlsplit(client_id)
    if (p.scheme != "https" or not p.hostname or p.username or p.password or p.fragment or p.port not in (None, 443)
            or p.path in ("", "/") or len(client_id) > 512):
        raise OAuthError("invalid_client", "a client_id URL must be https, with a path")
    try:
        addrs = {ipaddress.ip_address(a[4][0]) for a in socket.getaddrinfo(p.hostname, 443, type=socket.SOCK_STREAM)}
    except (OSError, ValueError):
        raise OAuthError("invalid_client", "the app's address could not be resolved") from None
    if not addrs or any(not a.is_global or a.is_multicast for a in addrs):
        raise OAuthError("invalid_client", "the app's metadata must be on a public address")
    conn = _PinnedHTTPS(p.hostname, str(next(iter(addrs))), METADATA_TIMEOUT_S)
    try:
        conn.request("GET", (p.path or "/") + (f"?{p.query}" if p.query else ""),
                     headers={"Accept": "application/json", "User-Agent": "Tijori-OAuth"})
        r = conn.getresponse()
        body = r.read(METADATA_MAX_BYTES + 1)
        status, cache = r.status, r.getheader("cache-control") or ""
    except (OSError, http.client.HTTPException, ssl.SSLError):
        raise OAuthError("invalid_client", "the app's metadata could not be fetched") from None
    finally:
        conn.close()
    if status != 200 or len(body) > METADATA_MAX_BYTES:
        raise OAuthError("invalid_client", "the app's metadata could not be fetched")
    try:
        doc = json.loads(body)
    except ValueError:
        raise OAuthError("invalid_client", "the app's metadata is not JSON") from None
    if not isinstance(doc, dict) or doc.get("client_id") != client_id:
        raise OAuthError("invalid_client", "the app's metadata names a different client_id")
    uris = doc.get("redirect_uris")
    if not isinstance(uris, list) or not 0 < len(uris) <= 10:
        raise OAuthError("invalid_client", "the app's metadata lists no redirect_uris")
    if doc.get("token_endpoint_auth_method", "none") != "none":
        raise OAuthError("invalid_client", "only public clients (token_endpoint_auth_method none) are supported")
    m = re.search(r"max-age=(\d+)", cache)
    ttl = timedelta(seconds=min(max(int(m[1]) if m else 3600, 300), 86400))
    name = _clean(doc.get("client_name"), 120) or (p.hostname or "app")
    return {"client_name": name, "redirect_uris": [check_redirect_uri(u) for u in uris]}, ttl


def authenticate_client(s: Session, client_id: str | None, secret: str | None) -> OAuthClient:
    """Token and revocation endpoints: the client, with its secret checked when it has one."""
    row = s.get(OAuthClient, client_id) if client_id else None
    if row is None:
        raise OAuthError("invalid_client", "unknown client", 401)
    if row.secret_hash is not None and not (secret and hmac.compare_digest(row.secret_hash, sha256_hex(secret))):
        raise OAuthError("invalid_client", "client authentication failed", 401)
    return row


# --- authorization ---------------------------------------------------------------------------------------

def open_request(s: Session, client_id: str, redirect_uri: str, state: str | None, challenge: str, scope: str) -> str:
    """The pending request's id: the sign-in round trip carries only this."""
    now = datetime.now(UTC)
    s.execute(delete(OAuthRequest).where(OAuthRequest.expires_at < now))
    if (s.scalar(select(func.count()).select_from(OAuthRequest)) or 0) >= MAX_PENDING:
        raise OAuthError("temporarily_unavailable", "too many sign-ins in progress; try again soon", 503)
    rid = secrets.token_urlsafe(32)
    s.add(OAuthRequest(id_hash=sha256_hex(rid), client_id=client_id, redirect_uri=redirect_uri, state=state,
                       code_challenge=challenge, scope=scope, expires_at=now + REQUEST_TTL))
    s.flush()
    return rid


def pending(s: Session, rid: str) -> OAuthRequest | None:
    row = s.get(OAuthRequest, sha256_hex(rid))
    return row if row is not None and row.expires_at > datetime.now(UTC) else None


def bind_consent(row: OAuthRequest, ctx: MemberContext) -> str:
    """The consent form's CSRF token, tied to this member and request."""
    csrf = secrets.token_urlsafe(32)
    row.member_id, row.csrf_hash = ctx.member_id, sha256_hex(csrf)
    return csrf


@dataclass(frozen=True, slots=True)
class Decision:
    redirect_uri: str
    state: str | None
    code: str | None  # None: the member said no


def decide(s: Session, ctx: MemberContext, rid: str, csrf: str, allow: bool, allow_write: bool) -> Decision:
    """Consumes the pending request. Read is always granted with an allow; write only when ticked."""
    row = s.scalars(delete(OAuthRequest).where(OAuthRequest.id_hash == sha256_hex(rid)).returning(OAuthRequest)).first()
    if (row is None or row.expires_at <= datetime.now(UTC) or row.member_id != ctx.member_id or not row.csrf_hash
            or not hmac.compare_digest(row.csrf_hash, sha256_hex(csrf))):
        raise OAuthError("invalid_request", "this approval expired or was already used; start again from the app")
    if not allow:
        audit(s, ctx, ACTOR, "mcp.oauth.deny", f"oauth_client:{row.client_id[:200]}", {})
        return Decision(row.redirect_uri, row.state, None)
    scope = " ".join(x for x in row.scope.split() if x != WRITE_SCOPE or allow_write)
    code = secrets.token_urlsafe(32)
    s.add(OAuthCode(code_hash=sha256_hex(code), client_id=row.client_id, member_id=ctx.member_id,
                    household_id=ctx.household_id, redirect_uri=row.redirect_uri, code_challenge=row.code_challenge,
                    scope=scope, expires_at=datetime.now(UTC) + CODE_TTL))
    audit(s, ctx, ACTOR, "mcp.oauth.authorize", f"oauth_client:{row.client_id[:200]}", {"scope": scope})
    return Decision(row.redirect_uri, row.state, code)


# --- tokens ----------------------------------------------------------------------------------------------

def _issue(s: Session, grant: McpToken, ctx: MemberContext) -> dict[str, Any]:
    now = datetime.now(UTC)
    access, refresh = "tja_" + secrets.token_urlsafe(32), "tjr_" + secrets.token_urlsafe(32)
    s.execute(delete(OAuthToken).where(OAuthToken.grant_id == grant.id, OAuthToken.expires_at < now))
    for tok, kind, ttl in ((access, "access", ACCESS_TTL), (refresh, "refresh", REFRESH_TTL)):
        s.add(OAuthToken(token_hash=sha256_hex(tok), grant_id=grant.id, member_id=ctx.member_id,
                         household_id=ctx.household_id, kind=kind, expires_at=now + ttl))
    return {"access_token": access, "token_type": "Bearer", "expires_in": int(ACCESS_TTL.total_seconds()),
            "refresh_token": refresh, "scope": grant.scope}


def _end_grant(s: Session, grant_id: int) -> None:
    s.execute(update(McpToken).where(McpToken.id == grant_id, McpToken.revoked_at.is_(None))
              .values(revoked_at=datetime.now(UTC)))
    s.execute(delete(OAuthToken).where(OAuthToken.grant_id == grant_id))


def redeem_code(s: Session, client: OAuthClient, code: str | None, redirect_uri: str | None, verifier: str | None,
                public_url: str, resource_param: str | None) -> dict[str, Any]:
    """One live grant per member and app: connecting again replaces the old one."""
    row = s.scalars(delete(OAuthCode).where(OAuthCode.code_hash == sha256_hex(code or "")).returning(OAuthCode)).first()
    if (row is None or row.expires_at <= datetime.now(UTC) or row.client_id != client.client_id
            or row.redirect_uri != redirect_uri or not pkce_ok(verifier, row.code_challenge)):
        raise OAuthError("invalid_grant", "the code is invalid, expired or already used", keep=True)
    if not resource_ok(public_url, resource_param):
        raise OAuthError("invalid_target", "tokens here are only for this MCP server")
    ctx = MemberContext(row.member_id, row.household_id)
    set_member_context(s, ctx)
    old = s.scalars(select(McpToken.id).where(McpToken.member_id == ctx.member_id, McpToken.client_id == client.client_id,
                                              McpToken.revoked_at.is_(None))).all()
    for grant_id in old:
        _end_grant(s, grant_id)
    live = s.scalars(select(McpToken.id).where(McpToken.member_id == ctx.member_id, McpToken.client_id.is_not(None),
                                               McpToken.revoked_at.is_(None)).order_by(McpToken.id)).all()
    for grant_id in live[:max(0, len(live) - MAX_GRANTS + 1)]:  # the oldest connections make room
        _end_grant(s, grant_id)
    grant = McpToken(member_id=ctx.member_id, name=client.name[:60], client_id=client.client_id, scope=row.scope)
    s.add(grant)
    s.flush()
    client.last_used_at = datetime.now(UTC)
    audit(s, ctx, ACTOR, "mcp.oauth.connect", f"mcp_token:{grant.id}", {"client": client.name[:60], "scope": row.scope})
    return _issue(s, grant, ctx)


def refresh(s: Session, client: OAuthClient, token: str | None, public_url: str,
            resource_param: str | None) -> dict[str, Any]:
    """Rotates the refresh token. Presenting a used one again means it leaked: the grant ends."""
    row = s.get(OAuthToken, sha256_hex(token or ""))
    if row is None or row.kind != "refresh":
        raise OAuthError("invalid_grant", "unknown refresh token")
    if not resource_ok(public_url, resource_param):
        raise OAuthError("invalid_target", "tokens here are only for this MCP server")
    ctx = MemberContext(row.member_id, row.household_id)
    set_member_context(s, ctx)
    grant = s.get(McpToken, row.grant_id)
    if grant is None or grant.revoked_at is not None or grant.client_id != client.client_id:
        raise OAuthError("invalid_grant", "this connection was removed; connect again")
    if row.used_at is not None:
        _end_grant(s, grant.id)
        audit(s, ctx, ACTOR, "mcp.oauth.refresh_reuse", f"mcp_token:{grant.id}", {})
        raise OAuthError("invalid_grant", "refresh token already used; the connection was ended to be safe", keep=True)
    if row.expires_at <= datetime.now(UTC):
        raise OAuthError("invalid_grant", "refresh token expired; connect again")
    row.used_at = datetime.now(UTC)
    client.last_used_at = row.used_at
    return _issue(s, grant, ctx)


def revoke(s: Session, client: OAuthClient, token: str | None) -> None:
    """RFC 7009: either of a connection's tokens ends the whole connection. Unknown tokens are not an error."""
    row = s.get(OAuthToken, sha256_hex(token or ""))
    if row is None:
        return
    ctx = MemberContext(row.member_id, row.household_id)
    set_member_context(s, ctx)
    grant = s.get(McpToken, row.grant_id)
    if grant is not None and grant.client_id == client.client_id:
        _end_grant(s, grant.id)
        audit(s, ctx, ACTOR, "mcp.oauth.revoke", f"mcp_token:{grant.id}", {})


def protected_resource_metadata(public_url: str) -> dict[str, Any]:
    return {"resource": resource(public_url), "authorization_servers": [issuer(public_url)],
            "scopes_supported": list(SCOPES), "bearer_methods_supported": ["header"], "resource_name": "Tijori"}


def authorization_server_metadata(public_url: str) -> dict[str, Any]:
    base = issuer(public_url)
    return {"issuer": base, "authorization_endpoint": f"{base}/oauth/authorize", "token_endpoint": f"{base}/oauth/token",
            "registration_endpoint": f"{base}/oauth/register", "revocation_endpoint": f"{base}/oauth/revoke",
            "scopes_supported": list(SCOPES), "response_types_supported": ["code"], "response_modes_supported": ["query"],
            "grant_types_supported": ["authorization_code", "refresh_token"], "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": list(AUTH_METHODS),
            "revocation_endpoint_auth_methods_supported": list(AUTH_METHODS),
            "client_id_metadata_document_supported": True, "authorization_response_iss_parameter_supported": True}
