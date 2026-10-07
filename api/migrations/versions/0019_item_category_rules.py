"""Your own item categories: merchant_order_item.category_by marks one you set, and item_category_rule holds
"every time I buy this" rules by (source, name, unit). Both win over what an agent sends on its next push.

Expand-only (docs/deploy.md).

Revision ID: 0019
Revises: 0018
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("merchant_order_item", sa.Column("category_by", sa.String(8), nullable=True))
    op.create_table(
        "item_category_rule",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("member_id", sa.BigInteger(), nullable=False),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("unit", sa.String(60), nullable=True),
        sa.Column("category", sa.String(60), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["member_id"], ["member.id"], name=op.f("fk_item_category_rule_member_id_member"),
                                ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_item_category_rule")),
        sa.UniqueConstraint("member_id", "source", "name", "unit", name=op.f("uq_item_category_rule_member_id_source_name_unit"),
                            postgresql_nulls_not_distinct=True),
    )
    op.create_index(op.f("ix_item_category_rule_member_id"), "item_category_rule", ["member_id"])
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON item_category_rule TO tijori_app")
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO tijori_app")
    op.execute("ALTER TABLE item_category_rule ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE item_category_rule FORCE ROW LEVEL SECURITY")
    op.execute("""CREATE POLICY item_category_rule_own ON item_category_rule
        USING (member_id = tijori_current_member()) WITH CHECK (member_id = tijori_current_member())""")


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS item_category_rule_own ON item_category_rule")
    op.drop_table("item_category_rule")
    op.drop_column("merchant_order_item", "category_by")
