"""Onboarding: invites, onboarding state, IMAP mail sources, envelope-encrypted secrets.

`secret` gains the SecretBox envelope columns. It has no rows before this release, so the new
NOT NULL columns need no backfill.

Revision ID: 0004
Revises: 0003
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STEPS = ("profile", "mail", "statement_passwords", "first_upload", "done")


def _check(values: tuple[str, ...]) -> str:
    return ", ".join(repr(v) for v in values)


def upgrade() -> None:
    op.add_column("member", sa.Column("onboarding_step", sa.String(24), server_default="profile", nullable=False))
    op.create_check_constraint(op.f("ck_member_onboarding_step"), "member", f"onboarding_step IN ({_check(STEPS)})")
    op.add_column("member", sa.Column("onboarding_completed_at", sa.DateTime(timezone=True), nullable=True))
    op.execute("GRANT UPDATE (onboarding_step, onboarding_completed_at) ON member TO tijori_app")

    for name, type_ in (("key_version", sa.String(16)), ("wrapped_key", sa.LargeBinary()),
                        ("key_nonce", sa.LargeBinary()), ("nonce", sa.LargeBinary())):
        op.add_column("secret", sa.Column(name, type_, nullable=False))
    op.add_column("secret", sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
                                      nullable=False))

    op.create_table(
        "invite",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("household_id", sa.BigInteger(), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("created_by", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("used_by", sa.BigInteger(), nullable=True),
        sa.ForeignKeyConstraint(["household_id"], ["household.id"], name=op.f("fk_invite_household_id_household"),
                                ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["member.id"], name=op.f("fk_invite_created_by_member"),
                                ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["used_by"], ["member.id"], name=op.f("fk_invite_used_by_member"),
                                ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_invite")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_invite_token_hash")),
    )
    op.create_index(op.f("ix_invite_household_id"), "invite", ["household_id"])

    op.create_table(
        "mail_source",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("member_id", sa.BigInteger(), nullable=False),
        sa.Column("provider", sa.String(24), nullable=False),
        sa.Column("host", sa.String(253), nullable=False),
        sa.Column("port", sa.Integer(), nullable=False),
        sa.Column("username", sa.String(320), nullable=False),
        sa.Column("label", sa.String(100), server_default="tijori", nullable=False),
        sa.Column("status", sa.String(24), server_default="untested", nullable=False),
        sa.Column("last_tested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(40), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("provider IN ('gmail', 'outlook', 'yahoo', 'custom')", name=op.f("ck_mail_source_mail_provider")),
        sa.CheckConstraint("status IN ('untested', 'ok', 'error')", name=op.f("ck_mail_source_mail_status")),
        sa.ForeignKeyConstraint(["member_id"], ["member.id"], name=op.f("fk_mail_source_member_id_member"),
                                ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_mail_source")),
    )
    op.create_index(op.f("ix_mail_source_member_id"), "mail_source", ["member_id"])

    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON invite, mail_source TO tijori_app")
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO tijori_app")
    op.execute("ALTER TABLE mail_source ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE mail_source FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY mail_source_own ON mail_source
        USING (member_id = tijori_current_member()) WITH CHECK (member_id = tijori_current_member())""")
    # Household-scoped: members see their household's invites; the api lets only admins create.
    op.execute("ALTER TABLE invite ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE invite FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY invite_household ON invite
        USING (household_id = tijori_current_household())
        WITH CHECK (household_id = tijori_current_household() AND created_by = tijori_current_member())""")

    # The invite landing page, before sign-in: what the token is for, never other invites.
    op.execute("""
        CREATE FUNCTION invite_lookup(p_token_hash text)
        RETURNS TABLE(email text, household text, expires_at timestamptz, used boolean)
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS
        $$ SELECT i.email::text, h.name::text, i.expires_at, i.used_at IS NOT NULL
           FROM invite i JOIN household h ON h.id = i.household_id WHERE i.token_hash = p_token_hash $$""")
    # Sign-in with a valid invite for this exact email: join the household, burn the invite.
    op.execute("""
        CREATE FUNCTION auth_register_invited(p_email text, p_name text, p_token_hash text)
        RETURNS TABLE(member_id bigint, household_id bigint)
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
        DECLARE v_invite invite%ROWTYPE; v_member bigint;
        BEGIN
          SELECT * INTO v_invite FROM invite i
          WHERE i.token_hash = p_token_hash AND i.used_at IS NULL AND i.expires_at > now()
            AND lower(i.email) = lower(p_email)
          FOR UPDATE;
          IF NOT FOUND THEN RETURN; END IF;
          IF EXISTS (SELECT 1 FROM member m WHERE lower(m.email) = lower(p_email)) THEN RETURN; END IF;
          INSERT INTO member (household_id, name, email, role)
          VALUES (v_invite.household_id, p_name, lower(p_email), 'member') RETURNING id INTO v_member;
          UPDATE invite SET used_at = now(), used_by = v_member WHERE id = v_invite.id;
          RETURN QUERY SELECT v_member, v_invite.household_id;
        END $$""")
    for fn in ("invite_lookup(text)", "auth_register_invited(text, text, text)"):
        op.execute(f"REVOKE ALL ON FUNCTION {fn} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {fn} TO tijori_app")


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS auth_register_invited(text, text, text)")
    op.execute("DROP FUNCTION IF EXISTS invite_lookup(text)")
    op.drop_table("mail_source")
    op.drop_table("invite")
    for name in ("updated_at", "nonce", "key_nonce", "wrapped_key", "key_version"):
        op.drop_column("secret", name)
    op.execute("REVOKE UPDATE (onboarding_step, onboarding_completed_at) ON member FROM tijori_app")
    op.drop_column("member", "onboarding_completed_at")
    op.drop_constraint(op.f("ck_member_onboarding_step"), "member")
    op.drop_column("member", "onboarding_step")
