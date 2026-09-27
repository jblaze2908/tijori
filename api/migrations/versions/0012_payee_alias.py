"""payee_alias: the member's own name for a payee, e.g. a shop paid through its owner's UPI handle.

One row per (member, payee_key). Payees given the same name group as one merchant. `original` is the
name the txns had before, restored when the alias is reset. Member-owned under RLS like every member table.

Expand-only (docs/deploy.md).

Revision ID: 0012
Revises: 0011
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "payee_alias",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("member_id", sa.BigInteger(), nullable=False),
        sa.Column("payee_key", sa.String(80), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("original", sa.String(120), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["member_id"], ["member.id"], name=op.f("fk_payee_alias_member_id_member"),
                                ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_payee_alias")),
        sa.UniqueConstraint("member_id", "payee_key", name=op.f("uq_payee_alias_member_id_payee_key")),
    )
    op.create_index(op.f("ix_payee_alias_member_id"), "payee_alias", ["member_id"])
    op.create_index(op.f("ix_payee_alias_member_name"), "payee_alias", ["member_id", "name"])
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON payee_alias TO tijori_app")
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO tijori_app")
    op.execute("ALTER TABLE payee_alias ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE payee_alias FORCE ROW LEVEL SECURITY")
    op.execute("""CREATE POLICY payee_alias_own ON payee_alias
        USING (member_id = tijori_current_member()) WITH CHECK (member_id = tijori_current_member())""")


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS payee_alias_own ON payee_alias")
    op.drop_index(op.f("ix_payee_alias_member_name"), "payee_alias")
    op.drop_index(op.f("ix_payee_alias_member_id"), "payee_alias")
    op.drop_table("payee_alias")
