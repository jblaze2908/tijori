"""Live net worth and source health: member-set component values, and the last IMAP message count.

Expand-only (docs/deploy.md): a new table and a nullable column, so the previous release keeps
working against this schema on rollback.

Revision ID: 0005
Revises: 0004
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

KEYS = ("sbi", "hdfc", "fd", "stocks", "mf", "ppf", "epf", "gold", "other")


def upgrade() -> None:
    op.create_table(
        "component_value",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("member_id", sa.BigInteger(), nullable=False),
        sa.Column("key", sa.String(24), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("as_of", sa.Date(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["member_id"], ["member.id"], name=op.f("fk_component_value_member_id_member"),
                                ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_component_value")),
        sa.UniqueConstraint("member_id", "key", "as_of", name=op.f("uq_component_value_member_id_key_as_of")),
        sa.CheckConstraint(f"key IN ({', '.join(repr(k) for k in KEYS)})", name=op.f("ck_component_value_known_key")),
        sa.CheckConstraint("amount >= 0", name=op.f("ck_component_value_amount_non_negative")),
    )
    op.create_index(op.f("ix_component_value_member_id"), "component_value", ["member_id"])
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON component_value TO tijori_app")
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO tijori_app")
    op.execute("ALTER TABLE component_value ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE component_value FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY component_value_own ON component_value
        USING (member_id = tijori_current_member()) WITH CHECK (member_id = tijori_current_member())""")

    op.add_column("mail_source", sa.Column("last_message_count", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("mail_source", "last_message_count")
    op.execute("DROP POLICY IF EXISTS component_value_own ON component_value")
    op.drop_index(op.f("ix_component_value_member_id"), table_name="component_value")
    op.drop_table("component_value")
