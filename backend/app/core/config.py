"""Application settings loaded from environment variables / .env."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(_REPO_ROOT / ".env", Path(".env")),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "AI Solar Intelligence Platform"
    app_env: str = "development"
    log_level: str = "INFO"
    secret_key: str = "dev-secret-change-me"
    access_token_expire_minutes: int = 720
    cors_origins: str = "http://localhost:3000"

    admin_email: str = "admin@example.com"
    admin_password: str = "change-me"

    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/solar"

    # GoodWe SEMS (unofficial app/web API)
    sems_account: str | None = None
    sems_password: str | None = None
    sems_station_id: str | None = None
    sems_sync_enabled: bool = False
    sems_sync_interval_seconds: int = 900

    # Weather
    weather_sync_enabled: bool = True
    weather_sync_interval_seconds: int = 3600

    # LLM for the AI Energy Analyst (provider-agnostic; any OpenAI-compatible free API works)
    llm_provider: str = "openai_compatible"  # openai_compatible | anthropic | none
    llm_base_url: str | None = (
        None  # e.g. https://api.groq.com/openai/v1, https://openrouter.ai/api/v1, http://localhost:11434/v1
    )
    llm_api_key: str | None = None
    llm_model: str | None = None  # e.g. llama-3.3-70b-versatile, google/gemini-2.0-flash-exp:free, llama3.1
    llm_tool_mode: str = "auto"  # auto | native | context  (context = backend pre-fetches data; works without function calling)
    llm_max_tokens: int = 4000
    llm_max_tool_rounds: int = 6
    llm_timeout_seconds: float = 120.0
    # Anthropic (only used when LLM_PROVIDER=anthropic)
    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-opus-5-5"

    model_dir: Path = Field(default=_REPO_ROOT / "data" / "models")
    upload_dir: Path = Field(default=_REPO_ROOT / "data" / "uploads")

    @field_validator("database_url")
    @classmethod
    def _psycopg_driver(cls, v: str) -> str:
        # Hosted Postgres (Render, Heroku, Neon...) hands out postgres:// or postgresql:// URLs;
        # SQLAlchemy needs the explicit psycopg (v3) driver.
        for prefix in ("postgres://", "postgresql://"):
            if v.startswith(prefix):
                return "postgresql+psycopg://" + v[len(prefix) :]
        return v

    @field_validator("sems_sync_interval_seconds")
    @classmethod
    def _min_sync_interval(cls, v: int) -> int:
        # Never hammer the unofficial SEMS API: enforce a floor.
        return max(v, 300)

    @field_validator("model_dir", "upload_dir", mode="before")
    @classmethod
    def _resolve_path(cls, v):
        p = Path(v)
        if not p.is_absolute():
            p = (_REPO_ROOT / p).resolve()
        return p

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def sems_configured(self) -> bool:
        return bool(self.sems_account and self.sems_password)

    @property
    def anthropic_configured(self) -> bool:
        return bool(self.anthropic_api_key)

    @property
    def llm_configured(self) -> bool:
        if self.llm_provider == "anthropic":
            return self.anthropic_configured
        if self.llm_provider == "openai_compatible":
            # Local servers (Ollama, LM Studio) need a base URL but no key
            return bool(self.llm_model) and bool(self.llm_api_key or self.llm_base_url)
        return False

    @property
    def llm_model_name(self) -> str | None:
        if self.llm_provider == "anthropic":
            return self.anthropic_model
        return self.llm_model


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.model_dir.mkdir(parents=True, exist_ok=True)
    s.upload_dir.mkdir(parents=True, exist_ok=True)
    return s
