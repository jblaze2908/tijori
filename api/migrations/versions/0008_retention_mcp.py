"""Raw-file retention and MCP tokens.

- raw_message.purged_at / raw_attachment.purged_at: the stored file was deleted under the member's
  retention setting; the row (and everything parsed from it) stays.
- mcp_token: per-member bearer tokens for the MCP endpoint, stored as SHA-256 only. RLS like every
  member table; mcp_member_by_token() is the one SECURITY DEFINER lookup, as auth_member_by_email is.

Expand-only (docs/deploy.md).

Revision ID: 0008
Revises: 0007
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("raw_message", sa.Column("purged_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("raw_attachment", sa.Column("purged_at", sa.DateTime(timezone=True), nullable=True))
    op.create_table(
        "mcp_token",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("member_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(60), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["member_id"], ["member.id"], name=op.f("fk_mcp_token_member_id_member"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_mcp_token")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_mcp_token_token_hash")),
    )
    op.create_index(op.f("ix_mcp_token_member_id"), "mcp_token", ["member_id"])
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON mcp_token TO tijori_app")
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO tijori_app")
    op.execute("ALTER TABLE mcp_token ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE mcp_token FORCE ROW LEVEL SECURITY")
    op.execute("""CREATE POLICY mcp_token_own ON mcp_token
        USING (member_id = tijori_current_member()) WITH CHECK (member_id = tijori_current_member())""")
    op.execute("""
        CREATE FUNCTION mcp_member_by_token(p_hash text)
        RETURNS TABLE(member_id bigint, household_id bigint, token_id bigint)
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS
        $$ SELECT m.id, m.household_id, t.id FROM mcp_token t JOIN member m ON m.id = t.member_id
           WHERE t.token_hash = p_hash AND t.revoked_at IS NULL $$""")
    op.execute("REVOKE ALL ON FUNCTION mcp_member_by_token(text) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION mcp_member_by_token(text) TO tijori_app")


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS mcp_member_by_token(text)")
    op.execute("DROP POLICY IF EXISTS mcp_token_own ON mcp_token")
    op.drop_index(op.f("ix_mcp_token_member_id"), table_name="mcp_token")
    op.drop_table("mcp_token")
    op.drop_column("raw_attachment", "purged_at")
    op.drop_column("raw_message", "purged_at")
