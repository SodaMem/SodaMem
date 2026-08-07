"""Server configuration (pydantic-settings, env-driven).

Every knob is validated at startup and fails loudly on a bad value — the core
library's own `env_int`/`env_bool` helpers swallow malformed input and fall back
to defaults, which violates the no-silent-failures policy for anything an
operator sets deliberately (PRD R2.9). A typo in SODAMEM_PORT should stop the
process, not silently serve on 8000.
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SODAMEM_", env_file=".env", extra="ignore"
    )

    # --- storage -----------------------------------------------------------
    data_root: Path = Field(
        default=Path("./data"),
        description="Root directory holding one store directory per user_id.",
    )
    store_cache_max: int = Field(
        default=64, ge=1,
        description=(
            "LRU cap on concurrently-open user stores. Must comfortably exceed "
            "request concurrency so an in-flight store is never the eviction "
            "victim — the same reasoning the benchmark harness's client cache uses)."
        ),
    )
    # --- outbound webhooks (opt-in; empty url = no thread, no egress) ------
    webhook_url: str = Field(
        default="",
        description=(
            "POST every memory change here. Empty (the default) means no "
            "delivery thread and no outbound traffic at all."
        ),
    )
    webhook_secret: str = Field(
        default="",
        description=(
            "HMAC-SHA256 key for the X-SodaMem-Signature header. Empty omits "
            "the header rather than sending an unsigned-but-signature-shaped "
            "value the receiver might trust."
        ),
    )

    allow_purge: bool = Field(
        default=False,
        description=(
            "Enable the irreversible `DELETE /v1/memories/{id}?purge=true` "
            "path. OFF by default: ordinary deletes archive (tombstone, "
            "provenance retained), and an operator has to consciously opt in "
            "before this deployment can physically erase facts and their "
            "roles/edges/vectors. A right-to-erasure switch, not a convenience."
        ),
    )

    # --- auth --------------------------------------------------------------
    api_key: str = Field(
        default="",
        description="Shared secret for inbound requests (Bearer or X-API-Key).",
    )
    auth_disabled: bool = Field(
        default=False,
        description=(
            "Explicit opt-out for local development. Auth is ON by default: an "
            "unset api_key with auth enabled is a startup error, never a silently "
            "open server (mem0 made the same call after shipping it open)."
        ),
    )

    # --- control plane (ADR 0001) ------------------------------------------
    request_log_max: int = Field(
        default=10_000, ge=0,
        description=(
            "Row cap on the persisted request log, enforced in the same "
            "transaction as each insert. 0 disables the log entirely — a real "
            "off switch, not a very small cap. An operational table with no "
            "ceiling is a disk-exhaustion bug on a delay fuse (the "
            "audit_bundles incident, 0726)."
        ),
    )
    job_retention_max: int = Field(
        default=5_000, ge=1,
        description=(
            "Row cap on persisted async job records. Oldest are pruned first, "
            "so a caller polling a job it just submitted never loses to the cap."
        ),
    )

    # --- llm (needed for ingest extraction and the answer endpoint) --------
    llm_provider: Literal["openai", "anthropic", "deepseek", "gemini"] = "openai"
    llm_model: str = ""
    llm_api_key: str = ""
    llm_base_url: str = ""

    # --- server ------------------------------------------------------------
    host: str = "0.0.0.0"
    port: int = Field(default=8000, ge=1, le=65535)
    cors_origins: list[str] = Field(
        default_factory=list,
        description="Exact origins allowed by CORS (no '*' when auth is on).",
    )

    @field_validator("data_root")
    @classmethod
    def _resolve_root(cls, v: Path) -> Path:
        return v.expanduser().resolve()

    def require_auth_configured(self) -> None:
        """Fail closed at startup rather than per-request."""
        if not self.auth_disabled and not self.api_key:
            raise RuntimeError(
                "SODAMEM_API_KEY is required. Set it, or set "
                "SODAMEM_AUTH_DISABLED=true to run without auth (local dev only)."
            )


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def reset_settings_cache() -> None:
    """Test hook — lets a test rebuild Settings after monkeypatching env."""
    global _settings
    _settings = None
