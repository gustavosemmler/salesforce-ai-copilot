"""Settings loaded from environment variables (and an optional .env file)."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


def _as_bool(value: str | None) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    target_org: str | None = None
    instance_url: str | None = None
    access_token: str | None = None
    username: str | None = None
    password: str | None = None
    security_token: str | None = None
    domain: str = "login"
    allow_writes: bool = False
    max_rows: int = 200
    chroma_path: str = ".chroma"
    anthropic_model: str = "claude-sonnet-5-5"

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()
        env = os.environ.get
        return cls(
            target_org=env("SF_TARGET_ORG") or None,
            instance_url=env("SF_INSTANCE_URL") or None,
            access_token=env("SF_ACCESS_TOKEN") or None,
            username=env("SF_USERNAME") or None,
            password=env("SF_PASSWORD") or None,
            security_token=env("SF_SECURITY_TOKEN") or None,
            domain=env("SF_DOMAIN", "login"),
            allow_writes=_as_bool(env("SF_ALLOW_WRITES")),
            max_rows=int(env("SF_MAX_ROWS", "200")),
            chroma_path=env("CHROMA_PATH", ".chroma"),
            anthropic_model=env("ANTHROPIC_MODEL", "claude-sonnet-5-5"),
        )
