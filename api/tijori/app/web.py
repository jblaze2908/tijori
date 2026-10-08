"""Same-origin UI: the built web app with an SPA fallback, plus security headers on every response."""

from pathlib import Path
from urllib.parse import urlsplit

from starlette.exceptions import HTTPException
from starlette.responses import FileResponse, Response
from starlette.staticfiles import StaticFiles
from starlette.types import ASGIApp, Message, Receive, Scope, Send

RESERVED = ("api", "health", "mcp", "auth", "oauth", ".well-known")
# Self only: the UI bundles its fonts, so no third party sees a page load.
CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; font-src 'self'; "
       "img-src 'self' data:; connect-src 'self'; "
       "frame-ancestors 'none'; base-uri 'self'; form-action 'self'; object-src 'none'")


class SpaFiles(StaticFiles):
    """Static files from web/dist; unknown non-API GET paths get index.html (client routing)."""

    def __init__(self, directory: Path) -> None:
        super().__init__(directory=directory, html=True, check_dir=True)
        self.index = directory / "index.html"
        self.favicon = directory / "favicon.svg"

    async def get_response(self, path: str, scope: Scope) -> Response:
        if path.split("/", 1)[0] in RESERVED:
            raise HTTPException(404)
        try:
            return await super().get_response(path, scope)
        except HTTPException as exc:
            if exc.status_code != 404 or scope["method"] not in ("GET", "HEAD") or not self.index.exists():
                raise
            last = path.rsplit("/", 1)[-1]
            if last == "favicon.ico" and self.favicon.exists():
                return FileResponse(self.favicon, media_type="image/svg+xml")
            if "." in last:
                raise  # a missing asset is a 404, not the app shell
            return FileResponse(self.index)


class SecurityHeaders:
    """Pure ASGI middleware, so it adds no per-request task or body buffering."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        is_api = scope["path"].startswith(("/api", "/health", "/auth", "/mcp", "/oauth", "/.well-known"))

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                have = {k.lower() for k, _ in headers}  # a response's own CSP or caching wins (the consent page)
                headers += [(b"x-content-type-options", b"nosniff"), (b"referrer-policy", b"same-origin"),
                            (b"x-frame-options", b"DENY")]
                if is_api and b"cache-control" not in have:
                    headers.append((b"cache-control", b"no-store"))
                if not is_api and b"content-security-policy" not in have:
                    headers.append((b"content-security-policy", CSP.encode()))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_headers)


_CORS_EXACT = ("/mcp", "/oauth/token", "/oauth/register", "/oauth/revoke")
_CORS_HEADERS = frozenset({"authorization", "content-type", "accept", "mcp-protocol-version", "mcp-method", "mcp-name",
                           "mcp-session-id", "last-event-id"})


def origin_ok(origin: str) -> bool:
    """A browser MCP client's origin: https anywhere, or http on loopback. "null" and other schemes are refused.
    Every CORS path authenticates by bearer token or client credentials, never a cookie, so any such site is safe."""
    p = urlsplit(origin)
    if p.path or p.query or p.fragment or p.username or not p.hostname:
        return False
    return p.scheme == "https" or (p.scheme == "http" and p.hostname in ("localhost", "127.0.0.1", "::1"))


class MachineCors:
    """CORS for the paths MCP clients call from a browser, with no Allow-Credentials. Pure ASGI, like SecurityHeaders."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path", "")
        if scope["type"] != "http" or not (path in _CORS_EXACT or path.startswith("/.well-known/")):
            await self.app(scope, receive, send)
            return
        hdrs = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        origin = hdrs.get("origin", "")
        if not origin or not origin_ok(origin):
            await self.app(scope, receive, send)
            return
        allow = [(b"access-control-allow-origin", origin.encode()), (b"vary", b"Origin")]
        if scope["method"] == "OPTIONS" and "access-control-request-method" in hdrs:
            asked = [h.strip().lower() for h in hdrs.get("access-control-request-headers", "").split(",") if h.strip()]
            ok = [h for h in asked if h in _CORS_HEADERS or h.startswith("mcp-param-")]
            await send({"type": "http.response.start", "status": 204, "headers": allow + [
                (b"access-control-allow-methods", b"GET, POST, OPTIONS"),
                (b"access-control-allow-headers", ", ".join(ok).encode()), (b"access-control-max-age", b"600")]})
            await send({"type": "http.response.body", "body": b""})
            return

        async def send_with_cors(message: Message) -> None:
            if message["type"] == "http.response.start":
                message["headers"] = list(message.get("headers", [])) + allow + [
                    (b"access-control-expose-headers", b"WWW-Authenticate, MCP-Protocol-Version")]
            await send(message)

        await self.app(scope, receive, send_with_cors)
