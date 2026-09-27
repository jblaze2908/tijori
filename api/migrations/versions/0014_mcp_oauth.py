"""MCP OAuth: any MCP client can connect by signing in, not only those that take a pasted token.

oauth_client, oauth_request, oauth_code and oauth_token hold hashes and ids only, like oauth_state, so
they carry no RLS: they are read before a member is known. A connected app is an mcp_token row (the
grant) with client_id and scope, so Settings lists and revokes it like a pasted token. mcp_token_auth()
is the one SECURITY DEFINER lookup for both kinds; mcp_member_by_token() stays for old code.

Expand-only (docs/deploy.md).

Revision ID: 0014
Revises: 0013
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _ts(name: str, nullable: bool = True) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def upgrade() -> None:
    op.create_table(
        "oauth_client",
        sa.Column("client_id", sa.String(512), nullable=False),
        sa.Column("kind", sa.String(12), nullable=False),  # registered (RFC 7591) | metadata (a client ID document)
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("redirect_uris", ARRAY(sa.Text()), nullable=False),
        sa.Column("auth_method", sa.String(24), nullable=False),
        sa.Column("secret_hash", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        _ts("last_used_at"),
        _ts("expires_at"),  # metadata documents: when to fetch again
        sa.PrimaryKeyConstraint("client_id", name=op.f("pk_oauth_client")),
    )
    op.create_table(
        "oauth_request",
        sa.Column("id_hash", sa.String(64), nullable=False),
        sa.Column("client_id", sa.String(512), nullable=False),
        sa.Column("redirect_uri", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), nullable=True),
        sa.Column("code_challenge", sa.String(128), nullable=False),
        sa.Column("scope", sa.String(200), nullable=False),
        sa.Column("member_id", sa.BigInteger(), sa.ForeignKey("member.id", ondelete="CASCADE"), nullable=True),
        sa.Column("csrf_hash", sa.String(64), nullable=True),
        _ts("expires_at", nullable=False),
        sa.PrimaryKeyConstraint("id_hash", name=op.f("pk_oauth_request")),
    )
    op.create_index(op.f("ix_oauth_request_expires_at"), "oauth_request", ["expires_at"])
    op.create_table(
        "oauth_code",
        sa.Column("code_hash", sa.String(64), nullable=False),
        sa.Column("client_id", sa.String(512), nullable=False),
        sa.Column("member_id", sa.BigInteger(), sa.ForeignKey("member.id", ondelete="CASCADE"), nullable=False),
        sa.Column("household_id", sa.BigInteger(), nullable=False),
        sa.Column("redirect_uri", sa.Text(), nullable=False),
        sa.Column("code_challenge", sa.String(128), nullable=False),
        sa.Column("scope", sa.String(200), nullable=False),
        _ts("expires_at", nullable=False),
        sa.PrimaryKeyConstraint("code_hash", name=op.f("pk_oauth_code")),
    )
    op.add_column("mcp_token", sa.Column("client_id", sa.String(512), nullable=True))
    op.add_column("mcp_token", sa.Column("scope", sa.String(200), nullable=True))  # null: everything
    op.alter_column("mcp_token", "token_hash", nullable=True)  # a connected app's grant has no static token
    op.create_table(
        "oauth_token",
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("grant_id", sa.BigInteger(), sa.ForeignKey("mcp_token.id", ondelete="CASCADE"), nullable=False),
        sa.Column("member_id", sa.BigInteger(), sa.ForeignKey("member.id", ondelete="CASCADE"), nullable=False),
        sa.Column("household_id", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.String(8), nullable=False),  # access | refresh
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        _ts("expires_at", nullable=False),
        _ts("used_at"),  # a refresh token is single use; a second use revokes the grant
        sa.PrimaryKeyConstraint("token_hash", name=op.f("pk_oauth_token")),
    )
    op.create_index(op.f("ix_oauth_token_grant_id"), "oauth_token", ["grant_id"])
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON oauth_client, oauth_request, oauth_code, oauth_token TO tijori_app")
    op.execute("""
        CREATE FUNCTION mcp_token_auth(p_hash text)
        RETURNS TABLE(member_id bigint, household_id bigint, token_id bigint, scope text)
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS
        $$ SELECT m.id, m.household_id, t.id, t.scope::text FROM mcp_token t JOIN member m ON m.id = t.member_id
           WHERE t.token_hash = p_hash AND t.revoked_at IS NULL
           UNION ALL
           SELECT m.id, m.household_id, t.id, t.scope::text FROM oauth_token o
             JOIN mcp_token t ON t.id = o.grant_id JOIN member m ON m.id = t.member_id
           WHERE o.token_hash = p_hash AND o.kind = 'access' AND o.expires_at > now() AND t.revoked_at IS NULL $$""")
    op.execute("REVOKE ALL ON FUNCTION mcp_token_auth(text) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION mcp_token_auth(text) TO tijori_app")


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS mcp_token_auth(text)")
    op.drop_table("oauth_token")
    op.execute("DELETE FROM mcp_token WHERE token_hash IS NULL")
    op.alter_column("mcp_token", "token_hash", nullable=False)
    op.drop_column("mcp_token", "scope")
    op.drop_column("mcp_token", "client_id")
    op.drop_table("oauth_code")
    op.drop_table("oauth_request")
    op.drop_table("oauth_client")
