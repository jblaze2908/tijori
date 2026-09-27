"""ops_event: what host jobs report back, such as each off-site backup run.

Not member data, so no RLS: the backup script inserts as the owner through the db container, and the app
only reads the latest rows.

Expand-only (docs/deploy.md).

Revision ID: 0011
Revises: 0010
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ops_event",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("ok", sa.Boolean(), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ops_event")),
    )
    op.create_index(op.f("ix_ops_event_kind_at"), "ops_event", ["kind", "at"])
    op.execute("GRANT SELECT ON ops_event TO tijori_app")


def downgrade() -> None:
    op.drop_table("ops_event")
