"""networth_note: the member's own note on a month of net worth. The old monthly snapshots carried one (and the
member's own reading of the month, `commentary`); 0020 moved their values onto accounts but left the words behind,
so they move here, joined when a month had both.

Expand-only (docs/deploy.md): snapshot keeps its remark and commentary for the previous release.

Revision ID: 0021
Revises: 0020
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "networth_note",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("member_id", sa.BigInteger(), nullable=False),
        sa.Column("month", sa.Date(), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["member_id"], ["member.id"], name=op.f("fk_networth_note_member_id_member"),
                                ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_networth_note")),
        sa.UniqueConstraint("member_id", "month", name=op.f("uq_networth_note_member_id_month")),
    )
    op.create_index(op.f("ix_networth_note_member_id"), "networth_note", ["member_id"])
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON networth_note TO tijori_app")
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO tijori_app")
    op.execute("ALTER TABLE networth_note ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE networth_note FORCE ROW LEVEL SECURITY")
    op.execute("""CREATE POLICY networth_note_own ON networth_note
        USING (member_id = tijori_current_member()) WITH CHECK (member_id = tijori_current_member())""")
    # Runs as the owner, so RLS hides nothing. A snapshot is dated within its month; the note goes on that month.
    op.execute("""
        INSERT INTO networth_note (member_id, month, note)
        SELECT member_id, date_trunc('month', date)::date,
               concat_ws(E'\\n\\n', nullif(btrim(remark), ''), nullif(btrim(commentary), ''))
        FROM snapshot
        WHERE nullif(btrim(remark), '') IS NOT NULL OR nullif(btrim(commentary), '') IS NOT NULL
        ON CONFLICT (member_id, month) DO NOTHING""")


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS networth_note_own ON networth_note")
    op.drop_table("networth_note")
