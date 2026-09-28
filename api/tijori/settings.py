"""Runtime configuration from TIJORI_* environment variables."""

import re
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from tijori.secretbox import SecretBox, parse_master_key


class Settings(BaseSettings):
    # hide_input_in_errors: a startup error must never echo a key, password or database URL.
    model_config = SettingsConfigDict(env_prefix="TIJORI_", extra="ignore", hide_input_in_errors=True)

    # Defaults to prod so a missing variable can never switch on dev-only behaviour.
    env: Literal["dev", "test", "prod"] = "prod"
    # Runtime role: no superuser, no BYPASSRLS, so row-level security applies.
    database_url: SecretStr
    # Owner role: migrations and CLI bootstrap only. Falls back to database_url.
    admin_database_url: SecretStr | None = None
    blob_dir: Path = Path("/data/blobs")
    # Built UI (index.html + assets); unset means the api serves the API alone.
    web_dist: Path | None = None
    max_upload_bytes: int = 15 * 1024 * 1024
    # Google sign-in. The redirect URI is {public_url}/auth/callback.
    public_url: str = "http://localhost:8310"
    oidc_client_id: str | None = None
    oidc_client_secret: SecretStr | None = None
    # The one Google account that may sign in. Every request is refused for any other member (deps.bind).
    owner_email: str | None = None
    # SecretBox master key: base64 of 32 random bytes. _OLD is only for rotation.
    master_key: SecretStr | None = None
    master_key_old: SecretStr | None = None
    # Self-hosted ntfy for member notifications (the worker posts; members pick a topic in Settings).
    ntfy_url: str | None = None
    ntfy_token: SecretStr | None = None

    @field_validator("owner_email")
    @classmethod
    def _owner_shape(cls, v: str | None) -> str | None:
        v = (v or "").strip().lower()
        if not v:
            return None
        if "," in v or not re.fullmatch(r"[^@\s]{1,64}@[^@\s]{1,255}", v):
            raise ValueError("must be exactly one email address")
        return v

    @field_validator("max_upload_bytes")
    @classmethod
    def _upload_bounds(cls, v: int) -> int:
        if not 1024 <= v <= 100 * 1024 * 1024:
            raise ValueError("must be between 1 KiB and 100 MiB")
        return v

    @field_validator("master_key", "master_key_old")
    @classmethod
    def _key_shape(cls, v: SecretStr | None) -> SecretStr | None:
        # Compose passes unset optional keys as "", which means "no key", not a malformed one.
        if v is None or not v.get_secret_value():
            return None
        parse_master_key(v.get_secret_value())  # ValueError names the rule, never the value
        return v

    @model_validator(mode="after")
    def _prod_needs_sign_in(self) -> "Settings":
        if self.env == "prod":
            if self.master_key is None:
                raise ValueError("prod needs TIJORI_MASTER_KEY (base64 of 32 bytes)")
            if self.owner_email is None:
                raise ValueError("prod needs TIJORI_OWNER_EMAIL (the one account that may sign in)")
            if not (self.oidc_client_id and self.oidc_client_secret):
                raise ValueError("prod needs TIJORI_OIDC_CLIENT_ID and TIJORI_OIDC_CLIENT_SECRET")
            if not self.public_url.startswith("https://"):
                raise ValueError("prod needs an https:// TIJORI_PUBLIC_URL")
        return self

    @property
    def dev_auth_enabled(self) -> bool:
        return self.env in ("dev", "test")

    def admin_url(self) -> str:
        return (self.admin_database_url or self.database_url).get_secret_value()

    def secret_box(self) -> SecretBox | None:
        if self.master_key is None:
            return None
        old = parse_master_key(self.master_key_old.get_secret_value()) if self.master_key_old else None
        return SecretBox(parse_master_key(self.master_key.get_secret_value()), old)


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
