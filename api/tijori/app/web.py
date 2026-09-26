"""Same-origin UI: the built web app with an SPA fallback, plus security headers on every response."""

from pathlib import Path

from starlette.exceptions import HTTPException
from starlette.responses import FileResponse, Response
from starlette.staticfiles import StaticFiles
from starlette.types import ASGIApp, Message, Receive, Scope, Send

RESERVED = ("api", "health", "mcp", "auth")
# Self only: the UI bundles its fonts, so no third party sees a page load (PLAN §10).
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
        is_api = scope["path"].startswith(("/api", "/health", "/auth"))

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers += [(b"x-content-type-options", b"nosniff"), (b"referrer-policy", b"same-origin"),
                            (b"x-frame-options", b"DENY")]
                headers.append((b"cache-control", b"no-store") if is_api else
                               (b"content-security-policy", CSP.encode()))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_headers)
