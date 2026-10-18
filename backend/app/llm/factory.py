"""Builds the active provider from DB settings (admin-editable) falling back to env."""
from __future__ import annotations

import threading

from ..config import Settings
from ..db import Database
from ..security import SecretBox
from .base import LLMError, LLMProvider
from .mock import MockProvider

PROVIDERS = ("mock", "bedrock", "azure_openai")


class LLMManager:
    def __init__(self, settings: Settings, db: Database, secrets: SecretBox):
        self.settings, self.db, self.secrets = settings, db, secrets
        self._lock = threading.Lock()
        self._cached: LLMProvider | None = None
        self.override: LLMProvider | None = None  # tests

    def config(self) -> dict:
        s = self.settings
        stored = self.db.get_setting("llm", {}) or {}
        return {
            "provider": stored.get("provider", s.llm_provider),
            "bedrock_model_id": stored.get("bedrock_model_id", s.bedrock_model_id),
            "aws_region": stored.get("aws_region", s.aws_region),
            "azure_openai_endpoint": stored.get("azure_openai_endpoint", s.azure_openai_endpoint),
            "azure_openai_deployment": stored.get("azure_openai_deployment", s.azure_openai_deployment),
            "azure_openai_api_version": stored.get("azure_openai_api_version", s.azure_openai_api_version),
        }

    def public_config(self) -> dict:
        c = self.config()
        enc = self.db.get_setting("llm_secrets_enc")
        sec = self.secrets.decrypt(enc) if enc else {}
        c["has_azure_api_key"] = bool(sec.get("azure_api_key"))
        c["has_aws_keys"] = bool(sec.get("access_key_id"))
        return c

    def update(self, data: dict, secret_updates: dict | None = None) -> None:
        allowed = {"provider", "bedrock_model_id", "aws_region", "azure_openai_endpoint",
                   "azure_openai_deployment", "azure_openai_api_version"}
        if data.get("provider") and data["provider"] not in PROVIDERS:
            raise ValueError(f"provider must be one of {PROVIDERS}")
        cur = self.db.get_setting("llm", {}) or {}
        cur.update({k: v for k, v in data.items() if k in allowed and v is not None})
        self.db.set_setting("llm", cur)
        if secret_updates:
            enc = self.db.get_setting("llm_secrets_enc")
            sec = self.secrets.decrypt(enc) if enc else {}
            sec.update({k: v for k, v in secret_updates.items() if v})
            self.db.set_setting("llm_secrets_enc", self.secrets.encrypt(sec))
        with self._lock:
            self._cached = None

    def get(self) -> LLMProvider:
        if self.override is not None:
            return self.override
        with self._lock:
            if self._cached is None:
                self._cached = self._build()
            return self._cached

    def _build(self) -> LLMProvider:
        c = self.config()
        enc = self.db.get_setting("llm_secrets_enc")
        sec = self.secrets.decrypt(enc) if enc else {}
        p = c["provider"]
        if p == "bedrock":
            from .bedrock import BedrockProvider
            return BedrockProvider(c["bedrock_model_id"], c["aws_region"], creds=sec)
        if p == "azure_openai":
            from .azure_openai import AzureOpenAIProvider
            return AzureOpenAIProvider(c["azure_openai_endpoint"], c["azure_openai_deployment"],
                                       c["azure_openai_api_version"], api_key=sec.get("azure_api_key"))
        if p == "mock":
            return MockProvider()
        raise LLMError("config", f"Unknown provider {p}")

    def safe_get(self) -> LLMProvider:
        """Never raises: misconfiguration falls back to the mock so the app stays usable."""
        try:
            return self.get()
        except Exception:  # noqa: BLE001
            return MockProvider()
