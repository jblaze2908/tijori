"""MCP access: pasted bearer tokens (shown once, stored as SHA-256, at most 10 live per member) and the grants
of apps connected by signing in (services/mcp_oauth.py). Both are listed and revoked here."""

import hashlib
import secrets
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from tijori.db import MemberContext
from tijori.models import McpToken
from tijori.services.common import audit
from tijori.services.errors import Invalid, NotFound
from tijori.services.mcp_oauth import can_write

MAX_LIVE = 10


def _out(t: McpToken) -> dict[str, Any]:
    return {"id": t.id, "name": t.name, "created_at": t.created_at, "last_used_at": t.last_used_at,
            "revoked": t.revoked_at is not None, "kind": "app" if t.client_id else "token", "can_write": can_write(t.scope)}


def list_tokens(s: Session, member_id: int) -> list[dict[str, Any]]:
    return [_out(t) for t in s.scalars(select(McpToken).where(McpToken.member_id == member_id).order_by(McpToken.id.desc()))]


def create(s: Session, ctx: MemberContext, actor: str, name: str) -> dict[str, Any]:
    live = s.scalar(select(func.count()).where(McpToken.member_id == ctx.member_id, McpToken.revoked_at.is_(None),
                                               McpToken.token_hash.is_not(None)))  # connected apps don't count
    if live >= MAX_LIVE:
        raise Invalid(f"at most {MAX_LIVE} live tokens; revoke one first")
    token = "tjm_" + secrets.token_urlsafe(32)
    t = McpToken(member_id=ctx.member_id, name=name, token_hash=hashlib.sha256(token.encode()).hexdigest())
    s.add(t)
    s.flush()
    audit(s, ctx, actor, "mcp.token.create", f"mcp_token:{t.id}", {"name": name})
    return {**_out(t), "token": token}


def revoke(s: Session, ctx: MemberContext, actor: str, token_id: int) -> dict[str, Any]:
    n = s.execute(update(McpToken).where(McpToken.member_id == ctx.member_id, McpToken.id == token_id,
                                         McpToken.revoked_at.is_(None)).values(revoked_at=datetime.now(UTC))).rowcount
    if not n:
        raise NotFound("token not found")
    audit(s, ctx, actor, "mcp.token.revoke", f"mcp_token:{token_id}", {})
    return {"id": token_id, "revoked": True}
