"""Authentication.

- Prod: a server-side session from Google sign-in (see oidc.py). The cookie holds a random id;
  the database holds only its SHA-256.
- Dev/test only: additionally a trusted email header. The dev provider refuses to construct
  unless TIJORI_ENV is dev or test, and `build_auth` never selects it in prod.

Cookie-authenticated writes also need a same-origin Origin (or Referer) header: SameSite=Lax
already stops cross-site POSTs in current browsers, and this closes the gap for older ones.
"""

import re
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlsplit

from fastapi import HTTPException, Request, status

from tijori.services.common import sha256_hex
from tijori.settings import Settings

DEV_HEADER = "X-Tijori-Dev-Member"
_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,255}$")
SAFE_METHODS = ("GET", "HEAD", "OPTIONS")


def session_cookie_name(settings: Settings) -> str:
    # __Host- makes the browser insist on Secure, Path=/ and no Domain: it can't be planted
    # from a sibling subdomain. Plain http (local dev) can't use the prefix.
    return "__Host-tijori_session" if settings.public_url.startswith("https://") else "tijori_session"


@dataclass(frozen=True, slots=True)
class Identity:
    kind: Literal["email", "session", "mcp"]
    value: str  # an email (dev header), a session id hash or an MCP token hash; never the raw secret


# Set only by the in-process MCP dispatch (app/mcp.py): no HTTP client can write an ASGI scope key.
MCP_IDENTITY = "tijori.mcp_identity"


def _origin(url: str) -> str:
    p = urlsplit(url)
    return f"{p.scheme}://{p.netloc}".lower()


def check_same_origin(request: Request, settings: Settings) -> None:
    sent = request.headers.get("origin") or request.headers.get("referer")
    if not sent or _origin(sent) != _origin(settings.public_url):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "cross-origin request refused")


class DevHeaderAuth:
    def __init__(self, settings: Settings) -> None:
        if not settings.dev_auth_enabled:
            raise RuntimeError("dev header auth is only available with TIJORI_ENV=dev or test")

    def authenticate(self, request: Request) -> Identity | None:
        email = request.headers.get(DEV_HEADER, "").strip()
        return Identity("email", email.lower()) if _EMAIL.match(email) else None


class SessionCookieAuth:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.cookie = session_cookie_name(settings)

    def authenticate(self, request: Request) -> Identity | None:
        raw = request.cookies.get(self.cookie, "")
        if not 32 <= len(raw) <= 128:
            return None
        if request.method not in SAFE_METHODS:
            check_same_origin(request, self.settings)
        return Identity("session", sha256_hex(raw))


class AuthChain:
    def __init__(self, *providers: DevHeaderAuth | SessionCookieAuth) -> None:
        self.providers = providers

    def authenticate(self, request: Request) -> Identity | None:
        return next((i for p in self.providers if (i := p.authenticate(request))), None)


def build_auth(settings: Settings) -> AuthChain:
    if settings.env == "prod":
        return AuthChain(SessionCookieAuth(settings))
    return AuthChain(DevHeaderAuth(settings), SessionCookieAuth(settings))


def authenticate(request: Request) -> Identity:
    identity = request.scope.get(MCP_IDENTITY) or request.app.state.auth.authenticate(request)
    if identity is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "not signed in")
    return identity
