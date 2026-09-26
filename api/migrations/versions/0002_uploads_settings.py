"""Uploads, settings and net worth v2: member.settings, txn.sources, one statement per period,
snapshot.note renamed to remark.

The rename is safe only because no release has shipped yet; after the first deploy, schema
changes are expand/contract (a failed deploy rolls back code, never the schema).

Revision ID: 0002
Revises: 0001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SOURCES = ("statement", "alert", "sms", "upload", "expected", "import")


def upgrade() -> None:
    op.add_column("member", sa.Column("settings", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"),
                                      nullable=False))
    op.execute("GRANT UPDATE (settings) ON member TO tijori_app")
    op.add_column("txn", sa.Column("sources", postgresql.ARRAY(sa.Text()), server_default=sa.text("'{}'::text[]"),
                                   nullable=False))
    op.create_check_constraint(
        op.f("ck_txn_known_sources"), "txn", f"sources <@ ARRAY[{', '.join(repr(s) for s in SOURCES)}]::text[]"
    )
    op.create_unique_constraint(
        op.f("uq_statement_member_id_account_id_period_start_period_end"), "statement",
        ["member_id", "account_id", "period_start", "period_end"],
    )
    op.alter_column("snapshot", "note", new_column_name="remark")


def downgrade() -> None:
    op.alter_column("snapshot", "remark", new_column_name="note")
    op.drop_constraint("uq_statement_member_id_account_id_period_start_period_end", "statement")
    op.drop_constraint("ck_txn_known_sources", "txn")
    op.drop_column("txn", "sources")
    op.execute("REVOKE UPDATE (settings) ON member FROM tijori_app")
    op.drop_column("member", "settings")
