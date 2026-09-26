"""Google sign-in: server-side sessions, pending-login state, and the two pre-login lookups.

auth_session is member-scoped under RLS like everything else; the only ways in before a member
context exists are the SECURITY DEFINER functions below, which return ids, never secrets.

Revision ID: 0003
Revises: 0002
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "auth_session",
        sa.Column("id_hash", sa.String(64), nullable=False),
        sa.Column("member_id", sa.BigInteger(), sa.ForeignKey("member.id", ondelete="CASCADE",
                                                              name=op.f("fk_auth_session_member_id_member")),
                  nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id_hash", name=op.f("pk_auth_session")),
    )
    op.create_index(op.f("ix_auth_session_member_id"), "auth_session", ["member_id"])
    op.create_table(
        "oauth_state",
        sa.Column("state_hash", sa.String(64), nullable=False),
        sa.Column("browser_hash", sa.String(64), nullable=False),
        sa.Column("nonce", sa.String(128), nullable=False),
        sa.Column("code_verifier", sa.String(128), nullable=False),
        sa.Column("invite_hash", sa.String(64), nullable=True),
        sa.Column("return_to", sa.String(200), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("state_hash", name=op.f("pk_oauth_state")),
    )
    op.create_index(op.f("ix_oauth_state_expires_at"), "oauth_state", ["expires_at"])

    op.execute("GRANT SELECT, INSERT, DELETE ON auth_session, oauth_state TO tijori_app")
    op.execute("ALTER TABLE auth_session ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE auth_session FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY auth_session_own ON auth_session
        USING (member_id = tijori_current_member()) WITH CHECK (member_id = tijori_current_member())""")
    # Cookie -> member, before any context exists. Expired sessions never resolve.
    op.execute("""
        CREATE FUNCTION auth_session_member(p_id_hash text)
        RETURNS TABLE(member_id bigint, household_id bigint, email text)
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS
        $$ SELECT m.id, m.household_id, m.email::text FROM auth_session s JOIN member m ON m.id = s.member_id
           WHERE s.id_hash = p_id_hash AND s.expires_at > now() $$""")
    # First sign-in of an allowlisted email (the app checks TIJORI_ALLOWED_EMAILS): a new
    # household with this member as admin. Idempotent for an existing member.
    op.execute("""
        CREATE FUNCTION auth_register_member(p_email text, p_name text)
        RETURNS TABLE(member_id bigint, household_id bigint)
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
        DECLARE v_household bigint;
        BEGIN
          RETURN QUERY SELECT m.id, m.household_id FROM member m WHERE lower(m.email) = lower(p_email);
          IF FOUND THEN RETURN; END IF;
          INSERT INTO household (name) VALUES (p_name || '''s household') RETURNING id INTO v_household;
          RETURN QUERY INSERT INTO member (household_id, name, email, role)
                       VALUES (v_household, p_name, lower(p_email), 'admin') RETURNING id, member.household_id;
        END $$""")
    for fn in ("auth_session_member(text)", "auth_register_member(text, text)"):
        op.execute(f"REVOKE ALL ON FUNCTION {fn} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {fn} TO tijori_app")


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS auth_register_member(text, text)")
    op.execute("DROP FUNCTION IF EXISTS auth_session_member(text)")
    op.drop_table("oauth_state")
    op.drop_table("auth_session")
