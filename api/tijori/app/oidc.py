"""Google sign-in: OIDC authorization-code flow with PKCE, state and nonce. No new dependencies.

The ID token comes straight from Google's token endpoint over TLS (certificate verified), so per
OIDC Core §3.1.3.7 its signature may be trusted from TLS; we still check iss, aud/azp, exp, iat,
nonce and email_verified. Tokens, codes and the client secret are never logged or stored.
"""

import base64
import hashlib
import hmac
import json
import secrets
import ssl
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session

from tijori.app.auth import check_same_origin, session_cookie_name
from tijori.bootstrap import seed_categories
from tijori.db import MemberContext, bind_member_by_email, bind_member_by_session, set_member_context
from tijori.models import AuthSession, OAuthState
from tijori.services.common import audit, sha256_hex
from tijori.settings import Settings

router = APIRouter(prefix="/auth")

AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
ISSUERS = ("accounts.google.com", "https://accounts.google.com")
LOGIN_COOKIE = "tijori_login"
STATE_TTL = timedelta(minutes=10)
SESSION_TTL = timedelta(days=30)
CLOCK_SKEW = timedelta(seconds=60)
MAX_PENDING_LOGINS = 1000  # bounds the unauthenticated writes /auth/login can cause


class OidcError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code  # safe to show: a fixed reason, never token material


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def redirect_uri(settings: Settings) -> str:
    return settings.public_url.rstrip("/") + "/auth/callback"


def _secure(settings: Settings) -> bool:
    return settings.public_url.startswith("https://")


def _safe_return_to(value: str | None) -> str | None:
    """Relative paths only: no scheme, no '//' host, no backslash tricks (open redirect)."""
    if not value or not value.startswith("/") or value.startswith("//") or "\\" in value:
        return None
    return value


def exchange_code(settings: Settings, code: str, verifier: str) -> dict[str, Any]:
    assert settings.oidc_client_id and settings.oidc_client_secret
    body = urlencode({
        "code": code, "client_id": settings.oidc_client_id,
        "client_secret": settings.oidc_client_secret.get_secret_value(), "redirect_uri": redirect_uri(settings),
        "grant_type": "authorization_code", "code_verifier": verifier,
    }).encode()
    req = urllib.request.Request(TOKEN_URL, data=body, method="POST", headers={
        "Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10, context=ssl.create_default_context()) as r:
            data = json.loads(r.read(64 * 1024))
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        raise OidcError("exchange_failed") from None
    token = data.get("id_token") if isinstance(data, dict) else None
    if not isinstance(token, str):
        raise OidcError("token_invalid")
    return decode_id_token(token)


def decode_id_token(token: str) -> dict[str, Any]:
    parts = token.split(".")
    if len(parts) != 3:
        raise OidcError("token_invalid")
    try:
        claims = json.loads(base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4)))
    except (ValueError, UnicodeDecodeError):
        raise OidcError("token_invalid") from None
    if not isinstance(claims, dict):
        raise OidcError("token_invalid")
    return claims


def validate_claims(claims: dict[str, Any], settings: Settings, nonce: str, now: datetime) -> str:
    """Returns the verified, lower-cased email."""
    if claims.get("iss") not in ISSUERS:
        raise OidcError("token_invalid")
    aud = claims.get("aud")
    if isinstance(aud, list):
        if settings.oidc_client_id not in aud or claims.get("azp") != settings.oidc_client_id:
            raise OidcError("token_invalid")
    elif aud != settings.oidc_client_id:
        raise OidcError("token_invalid")
    try:
        exp = datetime.fromtimestamp(int(claims["exp"]), UTC)
        iat = datetime.fromtimestamp(int(claims.get("iat", 0)), UTC)
    except (KeyError, TypeError, ValueError, OverflowError):
        raise OidcError("token_invalid") from None
    if exp + CLOCK_SKEW < now or iat - CLOCK_SKEW > now:
        raise OidcError("token_expired")
    if not hmac.compare_digest(str(claims.get("nonce", "")), nonce):
        raise OidcError("token_invalid")
    if claims.get("email_verified") not in (True, "true"):
        raise OidcError("email_unverified")
    email = claims.get("email")
    if not isinstance(email, str) or "@" not in email or len(email) > 320:
        raise OidcError("token_invalid")
    return email.lower()


def _fail(settings: Settings, reason: str) -> RedirectResponse:
    resp = RedirectResponse(f"/?auth_error={reason}", status_code=status.HTTP_302_FOUND)
    resp.delete_cookie(LOGIN_COOKIE, path="/auth", secure=_secure(settings), httponly=True, samesite="lax")
    return resp


@router.get("/login")
def login(request: Request, return_to: Annotated[str | None, Query(max_length=200)] = None) -> RedirectResponse:
    settings: Settings = request.app.state.settings
    if not (settings.oidc_client_id and settings.oidc_client_secret):
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Google sign-in is not configured")
    state, nonce, verifier, browser = (secrets.token_urlsafe(32), secrets.token_urlsafe(32),
                                       secrets.token_urlsafe(64), secrets.token_urlsafe(32))
    now = datetime.now(UTC)
    with Session(request.app.state.engine) as s, s.begin():
        s.execute(delete(OAuthState).where(OAuthState.expires_at < now))
        pending = s.scalar(select(func.count()).select_from(OAuthState)) or 0
        if pending >= MAX_PENDING_LOGINS:
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "too many sign-ins in progress; try again soon")
        s.add(OAuthState(state_hash=sha256_hex(state), browser_hash=sha256_hex(browser), nonce=nonce,
                         code_verifier=verifier,
                         return_to=_safe_return_to(return_to), expires_at=now + STATE_TTL))
    query = urlencode({
        "client_id": settings.oidc_client_id, "redirect_uri": redirect_uri(settings), "response_type": "code",
        "scope": "openid email profile", "state": state, "nonce": nonce,
        "code_challenge": _b64url(hashlib.sha256(verifier.encode()).digest()), "code_challenge_method": "S256",
        "prompt": "select_account",
    })
    resp = RedirectResponse(f"{AUTHORIZE_URL}?{query}", status_code=status.HTTP_302_FOUND)
    resp.set_cookie(LOGIN_COOKIE, browser, max_age=int(STATE_TTL.total_seconds()), path="/auth",
                    secure=_secure(settings), httponly=True, samesite="lax")
    return resp


def _resolve_member(s: Session, settings: Settings, email: str, name: str) -> MemberContext | None:
    """Only the owner gets in: their member, created with a new household on first sign-in (categories
    seeded, idempotent). Invites no longer register anyone."""
    if settings.owner_email is None or email != settings.owner_email:
        return None
    ctx = bind_member_by_email(s, email)
    if ctx is not None:
        return ctx
    row = s.execute(text("SELECT * FROM auth_register_member(:email, :name)"), {"email": email, "name": name}).one()
    registered = MemberContext(row.member_id, row.household_id)
    set_member_context(s, registered)
    seed_categories(s, registered.household_id)
    return registered


@router.get("/callback")
def callback(request: Request, code: Annotated[str | None, Query(max_length=2048)] = None,
             state: Annotated[str | None, Query(max_length=256)] = None,
             error: Annotated[str | None, Query(max_length=256)] = None) -> RedirectResponse:
    settings: Settings = request.app.state.settings
    if error or not code or not state:
        return _fail(settings, "cancelled" if error else "bad_request")
    browser = request.cookies.get(LOGIN_COOKIE, "")
    now = datetime.now(UTC)
    with Session(request.app.state.engine, expire_on_commit=False) as s, s.begin():
        pending = s.scalars(delete(OAuthState).where(OAuthState.state_hash == sha256_hex(state))
                            .returning(OAuthState)).first()  # single use
    if (pending is None or pending.expires_at < now or not browser
            or not hmac.compare_digest(pending.browser_hash, sha256_hex(browser))):
        return _fail(settings, "state_invalid")
    try:
        claims = exchange_code(settings, code, pending.code_verifier)
        email = validate_claims(claims, settings, pending.nonce, now)
    except OidcError as exc:
        return _fail(settings, exc.code)
    name = str(claims.get("name") or email.split("@")[0])[:120]

    token = secrets.token_urlsafe(32)
    cookie = session_cookie_name(settings)
    with Session(request.app.state.engine) as s, s.begin():
        ctx = _resolve_member(s, settings, email, name)
        if ctx is None:
            return _fail(settings, "not_invited")
        old = request.cookies.get(cookie)
        if old:  # rotate: this browser's previous session ends with the new sign-in
            s.execute(delete(AuthSession).where(AuthSession.id_hash == sha256_hex(old)))
        s.add(AuthSession(id_hash=sha256_hex(token), member_id=ctx.member_id, expires_at=now + SESSION_TTL))
        audit(s, ctx, email, "auth.sign_in", f"member:{ctx.member_id}", {"via": "google"})
    resp = RedirectResponse(pending.return_to or "/", status_code=status.HTTP_302_FOUND)
    resp.set_cookie(cookie, token, max_age=int(SESSION_TTL.total_seconds()), path="/", secure=_secure(settings),
                    httponly=True, samesite="lax")
    resp.delete_cookie(LOGIN_COOKIE, path="/auth", secure=_secure(settings), httponly=True, samesite="lax")
    return resp


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: Request) -> Response:
    settings: Settings = request.app.state.settings
    check_same_origin(request, settings)
    cookie = session_cookie_name(settings)
    raw = request.cookies.get(cookie)
    if raw:
        with Session(request.app.state.engine) as s, s.begin():
            found = bind_member_by_session(s, sha256_hex(raw))
            if found is not None:
                s.execute(delete(AuthSession).where(AuthSession.id_hash == sha256_hex(raw)))
                audit(s, found[0], found[1], "auth.sign_out", f"member:{found[0].member_id}")
    resp = Response(status_code=status.HTTP_204_NO_CONTENT)
    resp.delete_cookie(cookie, path="/", secure=_secure(settings), httponly=True, samesite="lax")
    return resp
