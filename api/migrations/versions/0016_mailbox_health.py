"""mail_source.last_ok_poll_at: when a poll last read the whole label, apart from last_poll_at (every attempt).

Coverage (services/coverage.py) trusts alerts only up to this time. Existing rows whose last attempt succeeded
take that attempt's time, so a deploy doesn't flash every mailbox as never read.

Expand-only (docs/deploy.md).

Revision ID: 0016
Revises: 0015
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("mail_source", sa.Column("last_ok_poll_at", sa.DateTime(timezone=True), nullable=True))
    op.execute("""
        UPDATE mail_source SET last_ok_poll_at = last_poll_at
        WHERE last_ok_poll_at IS NULL AND last_poll_at IS NOT NULL AND last_poll_error IS NULL AND status = 'ok'""")


def downgrade() -> None:
    op.drop_column("mail_source", "last_ok_poll_at")
