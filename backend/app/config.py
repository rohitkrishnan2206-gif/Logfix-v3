"""Runtime configuration. Everything comes from environment variables so the same
build runs on a laptop, in Docker, on EC2 or on an Azure VM."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _bool(name: str, default: bool = False) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(os.getenv("LOGFIX_DATA_DIR", "data")).resolve())
    env: str = field(default_factory=lambda: os.getenv("LOGFIX_ENV", "dev"))

    # Auth / crypto. If unset they are generated once and stored in data_dir (mode 600).
    jwt_secret: str | None = field(default_factory=lambda: os.getenv("LOGFIX_JWT_SECRET"))
    secret_key: str | None = field(default_factory=lambda: os.getenv("LOGFIX_SECRET_KEY"))
    admin_user: str = field(default_factory=lambda: os.getenv("LOGFIX_ADMIN_USER", "admin"))
    admin_password: str | None = field(default_factory=lambda: os.getenv("LOGFIX_ADMIN_PASSWORD"))
    token_ttl_minutes: int = field(default_factory=lambda: int(os.getenv("LOGFIX_TOKEN_TTL_MIN", "480")))
    cors_origins: list[str] = field(
        default_factory=lambda: [o.strip() for o in os.getenv("LOGFIX_CORS_ORIGINS", "").split(",") if o.strip()]
    )

    # Public base URL of this deployment (used in alert links), e.g. https://logfix.onrender.com
    public_url: str = field(default_factory=lambda: os.getenv("LOGFIX_PUBLIC_URL", "").rstrip("/"))

    # Code assistant: if set, repositories must live under one of these directories.
    repo_roots: list[str] = field(
        default_factory=lambda: [o.strip() for o in os.getenv("LOGFIX_REPO_ROOTS", "").split(",") if o.strip()]
    )

    # Background work
    disable_scheduler: bool = field(default_factory=lambda: _bool("LOGFIX_DISABLE_SCHEDULER"))
    scheduler_tick_seconds: int = field(default_factory=lambda: int(os.getenv("LOGFIX_TICK_SECONDS", "5")))
    log_retention_days: int = field(default_factory=lambda: int(os.getenv("LOGFIX_RETENTION_DAYS", "7")))

    # LLM defaults (can be overridden from the Settings page, stored in the DB)
    llm_provider: str = field(default_factory=lambda: os.getenv("LLM_PROVIDER", "mock"))  # mock|bedrock|azure_openai
    bedrock_model_id: str = field(
        default_factory=lambda: os.getenv("BEDROCK_MODEL_ID", "us.anthropic.claude-haiku-4-5-20251001-v1:0")
    )
    aws_region: str = field(default_factory=lambda: os.getenv("AWS_REGION", "us-east-1"))
    azure_openai_endpoint: str = field(default_factory=lambda: os.getenv("AZURE_OPENAI_ENDPOINT", ""))
    azure_openai_deployment: str = field(default_factory=lambda: os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o-mini"))
    azure_openai_api_version: str = field(
        default_factory=lambda: os.getenv("AZURE_OPENAI_API_VERSION", "2024-08-01-preview")
    )

    # Frontend build to serve (optional)
    frontend_dist: Path | None = field(default=None)

    def __post_init__(self) -> None:
        if self.frontend_dist is None:
            env_path = os.getenv("LOGFIX_FRONTEND_DIST")
            candidates = [Path(env_path)] if env_path else []
            here = Path(__file__).resolve().parent
            candidates += [here.parent.parent / "frontend" / "dist", Path("frontend/dist")]
            for c in candidates:
                if (c / "index.html").exists():
                    self.frontend_dist = c
                    break

    @property
    def db_path(self) -> Path:
        return self.data_dir / "logfix.db"


def load_settings() -> Settings:
    return Settings()
