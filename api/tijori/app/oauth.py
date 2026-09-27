"""OAuth endpoints for MCP clients (services/mcp_oauth.py): discovery metadata, client registration, the
authorize page (Google sign-in, then consent), token and revocation. No new dependencies: forms are parsed
with the stdlib, the consent page is plain HTML with no script.
"""

import base64
import binascii
import html
import json
import re
from collections.abc import Callable
from typing import Any, TypeVar
from urllib.parse import parse_qsl, unquote_plus, urlencode, urlsplit

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from tijori.app.deps import bind, run_as_member
from tijori.app.multipart import read_capped
from tijori.app.ratelimit import RateLimiter
from tijori.db import MemberContext
from tijori.models import Member, OAuthClient, OAuthRequest
from tijori.services import mcp_oauth as oauth
from tijori.services.mcp_oauth import LOOPBACK, WRITE_SCOPE, OAuthError

router = APIRouter()
REGISTRATIONS = RateLimiter(limit=60, window_s=3600)  # all callers together: registering is unauthenticated
NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}
T = TypeVar("T")


def _json(data: Any, status: int = 200) -> JSONResponse:
    return JSONResponse(data, status_code=status, headers=NO_STORE)


def _error(exc: OAuthError) -> JSONResponse:
    headers = {**NO_STORE, **({"WWW-Authenticate": 'Basic realm="tijori"'} if exc.status == 401 else {})}
    return JSONResponse({"error": exc.code, "error_description": exc.description}, status_code=exc.status, headers=headers)


def _txn(engine: Any, work: Callable[[Session], T]) -> T:
    """One transaction. An OAuthError marked keep still commits first: a failed code redemption must use the
    code up, and a replayed refresh token must end its grant."""
    with Session(engine) as s:
        with s.begin():
            try:
                return work(s)
            except OAuthError as exc:
                if not exc.keep:
                    raise
                kept = exc
        raise kept


async def _form(request: Request, cap: int = 16 * 1024) -> dict[str, str]:
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/x-www-form-urlencoded":
        raise OAuthError("invalid_request", "send application/x-www-form-urlencoded")
    raw = await read_capped(request, cap)
    try:
        pairs = parse_qsl(raw.decode("utf-8"), keep_blank_values=True)
    except (UnicodeDecodeError, ValueError):
        raise OAuthError("invalid_request", "the form is not valid UTF-8") from None
    out: dict[str, str] = {}
    for k, v in pairs:
        if k in out:  # RFC 6749 §3.1: a parameter must not repeat
            raise OAuthError("invalid_request", f"{k} is given more than once")
        out[k] = v
    return out


def _client_credentials(request: Request, form: dict[str, str]) -> tuple[str | None, str | None]:
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("basic "):
        return form.get("client_id"), form.get("client_secret")
    try:
        cid, _, secret = base64.b64decode(auth[6:].strip(), validate=True).decode().partition(":")
    except (binascii.Error, UnicodeDecodeError):
        raise OAuthError("invalid_client", "malformed Basic credentials", 401) from None
    cid, secret = unquote_plus(cid), unquote_plus(secret)
    if form.get("client_id") not in (None, cid):
        raise OAuthError("invalid_client", "client_id differs from the Basic credentials", 401)
    return cid, secret


@router.get("/.well-known/oauth-protected-resource")
@router.get("/.well-known/oauth-protected-resource/mcp")
def protected_resource(request: Request) -> JSONResponse:
    return JSONResponse(oauth.protected_resource_metadata(request.app.state.settings.public_url),
                        headers={"Cache-Control": "public, max-age=3600"})


@router.get("/.well-known/oauth-authorization-server")
def authorization_server(request: Request) -> JSONResponse:
    return JSONResponse(oauth.authorization_server_metadata(request.app.state.settings.public_url),
                        headers={"Cache-Control": "public, max-age=3600"})


@router.post("/oauth/register")
async def register(request: Request) -> JSONResponse:
    if not REGISTRATIONS.allow("all"):
        return _error(OAuthError("temporarily_unavailable", "too many registrations; try again later", 429))
    raw = await read_capped(request, 16 * 1024)
    try:
        meta = json.loads(raw)
        out = await run_in_threadpool(_txn, request.app.state.engine, lambda s: oauth.register(s, meta))
    except ValueError:
        return _error(OAuthError("invalid_client_metadata", "the body must be JSON"))
    except OAuthError as exc:
        return _error(exc)
    return _json(out, 201)


@router.post("/oauth/token")
async def token(request: Request) -> JSONResponse:
    public_url = request.app.state.settings.public_url
    try:
        form = await _form(request)
        cid, secret = _client_credentials(request, form)

        def work(s: Session) -> dict[str, Any]:
            client = oauth.authenticate_client(s, cid, secret)
            grant = form.get("grant_type")
            if grant == "authorization_code":
                return oauth.redeem_code(s, client, form.get("code"), form.get("redirect_uri"), form.get("code_verifier"),
                                         public_url, form.get("resource"))
            if grant == "refresh_token":
                return oauth.refresh(s, client, form.get("refresh_token"), public_url, form.get("resource"))
            raise OAuthError("unsupported_grant_type", "grant_type must be authorization_code or refresh_token")

        return _json(await run_in_threadpool(_txn, request.app.state.engine, work))
    except OAuthError as exc:
        return _error(exc)


@router.post("/oauth/revoke")
async def revoke(request: Request) -> Response:
    try:
        form = await _form(request)
        cid, secret = _client_credentials(request, form)
        await run_in_threadpool(_txn, request.app.state.engine,
                                lambda s: oauth.revoke(s, oauth.authenticate_client(s, cid, secret), form.get("token")))
    except OAuthError as exc:
        return _error(exc)
    return Response(status_code=200, headers=NO_STORE)


# --- the authorize page -----------------------------------------------------------------------------------

AUTHORIZE_PARAMS = ("response_type", "client_id", "redirect_uri", "code_challenge", "code_challenge_method", "state",
                    "scope", "resource", "request")


def _with_query(uri: str, params: dict[str, str | None]) -> str:
    return uri + ("&" if "?" in uri else "?") + urlencode({k: v for k, v in params.items() if v is not None})


def _back(request: Request, uri: str, state: str | None, **params: str) -> RedirectResponse:
    """To the app, with RFC 9207 iss so it can tell this server's answer from another's."""
    iss = oauth.issuer(request.app.state.settings.public_url)
    return RedirectResponse(_with_query(uri, {**params, "state": state, "iss": iss}), status_code=303, headers=NO_STORE)


@router.get("/oauth/authorize")
def authorize(request: Request) -> Response:
    """Starts a request (the app's parameters) or shows consent for one (?request=, after sign-in)."""
    q = request.query_params
    if any(len(q.getlist(k)) > 1 for k in AUTHORIZE_PARAMS):
        return _page("Can't connect this app", "The app's sign-in link repeats a parameter.")
    engine = request.app.state.engine
    rid = q.get("request")
    if rid is None:
        try:
            outcome = _txn(engine, lambda s: _start(s, request))
        except OAuthError as exc:
            return _page("Can't connect this app", exc.description)
        if isinstance(outcome, Response):
            return outcome
        rid = outcome
    try:
        return _txn(engine, lambda s: _consent(s, request, rid))
    except OAuthError as exc:
        return _page("Can't connect this app", exc.description)


def _start(s: Session, request: Request) -> str | Response:
    q = request.query_params
    public_url = request.app.state.settings.public_url
    cid, uri, state = q.get("client_id") or "", q.get("redirect_uri") or "", q.get("state")
    if not cid or not uri or len(cid) > 512 or len(uri) > 2000 or len(state or "") > 2000:
        return _page("Can't connect this app", "The app's sign-in link is incomplete. Try connecting again from the app.")
    known: OAuthClient | None = None
    if not oauth.is_metadata_client(cid):  # a metadata document is fetched only once someone has signed in
        known = oauth.client_for(s, cid, fetch=False)
        if known is None:
            return _page("Can't connect this app", "Tijori doesn't know this app. Remove the connection in the app and add it again.")
        if not oauth.redirect_matches(known.redirect_uris, uri):
            return _page("Can't connect this app", "The app asked to send your approval to an address it never registered.")
    challenge = q.get("code_challenge") or ""
    problem = None
    if q.get("response_type") != "code":
        problem = ("unsupported_response_type", "only response_type=code is supported")
    elif q.get("code_challenge_method") != "S256" or not re.fullmatch(r"[A-Za-z0-9_-]{43,128}", challenge):
        problem = ("invalid_request", "PKCE with code_challenge_method=S256 is required")
    elif not oauth.resource_ok(public_url, q.get("resource")):
        problem = ("invalid_target", "this server only issues tokens for its own MCP endpoint")
    if problem:
        if known is None:  # never redirect to an address nothing has vouched for
            return _page("Can't connect this app", problem[1])
        return _back(request, uri, state, error=problem[0], error_description=problem[1])
    return oauth.open_request(s, cid, uri, state, challenge, oauth.granted_scope(q.get("scope")))


def _consent(s: Session, request: Request, rid: str) -> Response:
    row = oauth.pending(s, rid)
    if row is None:
        return _page("This link expired", "Start connecting again from the app.")
    identity = request.app.state.auth.authenticate(request)
    ctx = None
    if identity is not None:
        try:
            ctx, _ = bind(s, identity)
        except HTTPException:
            ctx = None
    if ctx is None:
        return RedirectResponse("/auth/login?" + urlencode({"return_to": f"/oauth/authorize?request={rid}"}),
                                status_code=302, headers=NO_STORE)
    client = oauth.client_for(s, row.client_id, fetch=True)
    if client is None or not oauth.redirect_matches(client.redirect_uris, row.redirect_uri):
        return _page("Can't connect this app", "The app asked to send your approval to an address it never registered.")
    csrf = oauth.bind_consent(row, ctx)
    member = s.get(Member, ctx.member_id)
    return _consent_page(client, row, rid, csrf, member.name if member else "")


@router.post("/oauth/authorize")
async def decide(request: Request) -> Response:
    try:
        form = await _form(request, 4096)
    except OAuthError as exc:
        return _page("Can't connect this app", exc.description)
    try:
        identity = request.app.state.auth.authenticate(request)  # a cookie POST must be same-origin
    except HTTPException:
        identity = None
    if identity is None:
        return _page("You're signed out", "Sign in to Tijori, then start connecting again from the app.")

    def work(s: Session, ctx: MemberContext, _actor: str) -> oauth.Decision:
        return oauth.decide(s, ctx, form.get("request", ""), form.get("csrf", ""), form.get("decision") == "allow",
                            form.get("write") == "on")

    try:
        d = await run_in_threadpool(run_as_member, request.app.state.engine, identity, work)
    except (OAuthError, HTTPException) as exc:
        return _page("Can't connect this app", exc.description if isinstance(exc, OAuthError) else "Sign in again.")
    if d.code is None:
        return _back(request, d.redirect_uri, d.state, error="access_denied", error_description="the member said no")
    return _back(request, d.redirect_uri, d.state, code=d.code)


# --- HTML -------------------------------------------------------------------------------------------------

_STYLE = """
:root{--bg:#f6f5f2;--card:#fff;--ink:#1d1c1a;--mute:#6b6862;--line:#e3e0da;--acc:#2f6f4f;--warn:#8a5a00;--warnbg:#fff6e0}
@media (prefers-color-scheme:dark){:root{--bg:#141413;--card:#1e1d1b;--ink:#ecebe7;--mute:#a09d96;--line:#34322e;
--acc:#6fbf94;--warn:#f0c060;--warnbg:#2b2413}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,-apple-system,sans-serif}
main{max-width:440px;margin:0 auto;padding:40px 16px}.card{background:var(--card);border:1px solid var(--line);
border-radius:14px;padding:24px}h1{font-size:20px;margin:0 0 4px}p{margin:8px 0}.mute{color:var(--mute);font-size:13px}
.app{font-weight:600}.host{font-family:ui-monospace,monospace;font-size:13px;word-break:break-all}
.warn{background:var(--warnbg);color:var(--warn);border-radius:8px;padding:8px 10px;font-size:13px;margin:12px 0}
ul{padding-left:18px;margin:12px 0}label{display:flex;gap:8px;align-items:flex-start;margin:12px 0;cursor:pointer}
.row{display:flex;gap:8px;margin-top:20px}button{flex:1;font:inherit;padding:10px;border-radius:10px;
border:1px solid var(--line);background:var(--card);color:var(--ink);cursor:pointer}
button.ok{background:var(--acc);border-color:var(--acc);color:#fff}
"""


def _html(title: str, body: str, csp: str) -> HTMLResponse:
    doc = (f"<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content='width=device-width,"
           f"initial-scale=1'><title>{html.escape(title)} · Tijori</title><style>{_STYLE}</style></head>"
           f"<body><main><div class=card>{body}</div></main></body></html>")
    return HTMLResponse(doc, headers={**NO_STORE, "Content-Security-Policy": csp})


def _page(title: str, message: str) -> HTMLResponse:
    body = (f"<h1>{html.escape(title)}</h1><p>{html.escape(message)}</p>"
            "<p class=mute>Close this tab and try again from the app.</p>")
    return _html(title, body, "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")


def _destination(uri: str) -> tuple[str, str, str | None]:
    """(what to show, CSP form-action source, warning) for where the approval goes."""
    p = urlsplit(uri)
    if p.scheme in ("http", "https"):
        where = p.netloc
        warn = (f"This sends your approval to an app on this computer ({where}). Approve only if you just started "
                "connecting from an app here.") if p.hostname in LOOPBACK else None
        return where, f"{p.scheme}://{p.netloc}", warn
    return f"{p.scheme}:", f"{p.scheme}:", f"This sends your approval to an app on this device ({p.scheme}:)."


def _consent_page(client: OAuthClient, row: OAuthRequest, rid: str, csrf: str, member: str) -> HTMLResponse:
    name = html.escape(client.name)
    where, target, warn = _destination(row.redirect_uri)
    who = (f"Identified by <span class=host>{html.escape(urlsplit(client.client_id).netloc)}</span>."
           if client.kind == "metadata" else "This app registered itself, so Tijori can't confirm who made it.")
    write = (f"<label><input type=checkbox name=write checked> <span>Also let it make changes: file transactions, edit "
             f"loans, budgets, accounts and settings, and upload statements.</span></label>"
             if WRITE_SCOPE in row.scope.split() else "")
    body = (f"<h1>Connect <span class=app>{name}</span>?</h1>"
            f"<p class=mute>Signed in to Tijori as {html.escape(member)}.</p><p>{who}</p>"
            f"<p>It will be able to read your transactions, spending, subscriptions, loans, net worth and settings. "
            f"People's UPI handles are masked.</p>"
            f"<p class=mute>After you answer, you go back to <span class=host>{html.escape(where)}</span>.</p>"
            + (f"<div class=warn>{html.escape(warn)}</div>" if warn else "")
            + f"<form method=post action='/oauth/authorize'>{write}"
            f"<input type=hidden name=request value='{html.escape(rid, quote=True)}'>"
            f"<input type=hidden name=csrf value='{html.escape(csrf, quote=True)}'>"
            "<div class=row><button type=submit name=decision value=deny>Don't allow</button>"
            "<button type=submit name=decision value=allow class=ok>Allow</button></div></form>"
            "<p class=mute>Disconnect it anytime in Tijori: Settings → MCP.</p>")
    # form-action must name the app's address too, or the browser blocks the redirect after the POST.
    csp = (f"default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'; "
           f"form-action 'self' {target}")
    return _html(f"Connect {client.name}", body, csp)
