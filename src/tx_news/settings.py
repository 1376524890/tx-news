# Input: 环境变量（TXNEWS_* / DASHSCOPE_API_KEY / OpenAI兼容 LLM_*）+ config/config.yaml + config/sources.txt
# Output: Settings/FileSettings（包含 infra/模型/源列表等配置）
# Pos: 全局配置加载入口（变更时同步更新以上注释与所属目录 FOLDER.md）

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import AliasChoices, BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class FileSettings(BaseModel):
    crawler: dict[str, Any] = Field(default_factory=dict)
    retention: dict[str, Any] = Field(default_factory=dict)
    embedding: dict[str, Any] = Field(default_factory=dict)
    llm: dict[str, Any] = Field(default_factory=dict)
    tushare: dict[str, Any] = Field(default_factory=dict)
    event_windows_minutes: dict[str, int] = Field(default_factory=dict)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TXNEWS_", env_file=".env", extra="ignore")

    # Compute mode (used to choose local vLLM vs cloud fallback, and embedding device defaults)
    # Values: cpu | gpu | auto
    accelerator: str = Field(
        default="cpu",
        validation_alias=AliasChoices("TXNEWS_ACCELERATOR", "TXNEWS_DEVICE_MODE", "TXNEWS_GPU_MODE"),
    )

    # Embedding overrides (optional; prefer setting via env for multi-GPU binding)
    embedding_device: str | None = Field(
        default=None,
        validation_alias=AliasChoices("TXNEWS_EMBEDDING_DEVICE", "EMBEDDING_DEVICE"),
    )

    # infra
    pg_dsn: str = "postgresql+psycopg://txnews:txnews@localhost:5432/txnews"
    redis_url: str = "redis://localhost:6379/0"
    nats_url: str = "nats://localhost:4222"
    nats_stream: str = "txnews"

    s3_endpoint: str = "http://localhost:9000"
    s3_access_key: str = "txnews"
    s3_secret_key: str = "txnewssecret"
    s3_bucket: str = "txnews-raw"
    s3_region: str = "us-east-1"

    qdrant_url: str = "http://localhost:6333"
    qdrant_collection: str = "txnews_articles"

    # Per-user LLM config (stored in Redis, keyed by cookie uid)
    require_user_llm: bool = Field(
        default=False,
        validation_alias=AliasChoices("TXNEWS_REQUIRE_USER_LLM"),
    )
    user_llm_ttl_seconds: int = Field(
        default=60 * 60 * 24 * 30,  # 30 days
        validation_alias=AliasChoices("TXNEWS_USER_LLM_TTL_SECONDS"),
    )

    # LLM keys (backward-compatible)
    # - preferred: TXNEWS_LLM_API_KEY (OpenAI-compatible services)
    # - compatible: DASHSCOPE_API_KEY / TXNEWS_DASHSCOPE_API_KEY
    dashscope_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("DASHSCOPE_API_KEY", "TXNEWS_DASHSCOPE_API_KEY"),
    )

    llm_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "TXNEWS_LLM_API_KEY",
            "LLM_API_KEY",
            "OPENAI_API_KEY",
            "DASHSCOPE_API_KEY",
        ),
    )
    llm_base_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "TXNEWS_LLM_BASE_URL",
            "LLM_BASE_URL",
            "OPENAI_BASE_URL",
        ),
    )
    llm_model_name: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "TXNEWS_LLM_MODEL_NAME",
            "LLM_MODEL_NAME",
            "OPENAI_MODEL",
        ),
    )

    # LLM (role split): chat vs deep-analysis
    llm_chat_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "TXNEWS_LLM_CHAT_API_KEY",
            "LLM_CHAT_API_KEY",
        ),
    )
    llm_chat_base_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "TXNEWS_LLM_CHAT_BASE_URL",
            "LLM_CHAT_BASE_URL",
        ),
    )
    llm_chat_model_name: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "TXNEWS_LLM_CHAT_MODEL_NAME",
            "LLM_CHAT_MODEL_NAME",
        ),
    )
    llm_chat_timeout_seconds: int | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "TXNEWS_LLM_CHAT_TIMEOUT_SECONDS",
            "LLM_CHAT_TIMEOUT_SECONDS",
        ),
    )

    llm_deep_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "TXNEWS_LLM_DEEP_API_KEY",
            "LLM_DEEP_API_KEY",
        ),
    )
    llm_deep_base_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "TXNEWS_LLM_DEEP_BASE_URL",
            "LLM_DEEP_BASE_URL",
        ),
    )
    llm_deep_model_name: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "TXNEWS_LLM_DEEP_MODEL_NAME",
            "LLM_DEEP_MODEL_NAME",
        ),
    )
    llm_deep_timeout_seconds: int | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "TXNEWS_LLM_DEEP_TIMEOUT_SECONDS",
            "LLM_DEEP_TIMEOUT_SECONDS",
        ),
    )

    # local files
    config_dir: Path = Path("config")
    config_yaml: Path = Path("config/config.yaml")
    sources_txt: Path = Path("config/sources.txt")

    def _accelerator_mode(self) -> str:
        return (self.accelerator or "").strip().lower() or "cpu"

    def resolve_llm(self, file_cfg: "FileSettings") -> dict[str, Any]:
        llm = file_cfg.llm or {}
        chat_cfg = llm.get("chat") if isinstance(llm.get("chat"), dict) else llm
        base_url = (
            (self.llm_chat_base_url or "").strip()
            or (self.llm_base_url or "").strip()
            or str(chat_cfg.get("base_url") or "").strip()
            or "https://dashscope.aliyuncs.com/compatible-mode/v1"
        )
        model = (
            (self.llm_chat_model_name or "").strip()
            or (self.llm_model_name or "").strip()
            or str(chat_cfg.get("model") or "").strip()
            or "qwen3-max"
        )
        api_key = (
            (self.llm_chat_api_key or "").strip()
            or (self.llm_api_key or "").strip()
            or (self.dashscope_api_key or "").strip()
            or str(chat_cfg.get("api_key") or "").strip()
            or None
        )
        timeout_seconds = int(self.llm_chat_timeout_seconds or chat_cfg.get("timeout_seconds") or 60)
        provider = str(chat_cfg.get("provider") or "openai_compat")
        return {
            "provider": provider,
            "base_url": base_url,
            "model": model,
            "api_key": api_key,
            "timeout_seconds": timeout_seconds,
        }

    def resolve_llm_chat(self, file_cfg: "FileSettings") -> dict[str, Any]:
        return self.resolve_llm(file_cfg)

    def resolve_llm_deep(self, file_cfg: "FileSettings") -> dict[str, Any]:
        # CPU-only fallback: keep original online LLM behavior for deep analysis as a safe baseline.
        if self._accelerator_mode() == "cpu":
            return self.resolve_llm(file_cfg)

        llm = file_cfg.llm or {}
        deep_cfg_is_split = isinstance(llm.get("deep"), dict)
        deep_env_is_set = any(
            [
                (self.llm_deep_base_url or "").strip(),
                (self.llm_deep_model_name or "").strip(),
                (self.llm_deep_api_key or "").strip(),
                self.llm_deep_timeout_seconds is not None,
            ]
        )
        if not deep_cfg_is_split and not deep_env_is_set:
            return self.resolve_llm(file_cfg)

        deep_cfg = llm.get("deep") if deep_cfg_is_split else {}

        base_url = (
            (self.llm_deep_base_url or "").strip()
            or str(deep_cfg.get("base_url") or "").strip()
            or "http://127.0.0.1:9999/v1"
        )
        model = (
            (self.llm_deep_model_name or "").strip()
            or str(deep_cfg.get("model") or "").strip()
            or "deepseekr1-merged"
        )
        api_key = (self.llm_deep_api_key or "").strip() or str(deep_cfg.get("api_key") or "").strip() or None
        timeout_seconds = int(self.llm_deep_timeout_seconds or deep_cfg.get("timeout_seconds") or 120)
        provider = str(deep_cfg.get("provider") or "openai_compat")
        return {
            "provider": provider,
            "base_url": base_url,
            "model": model,
            "api_key": api_key,
            "timeout_seconds": timeout_seconds,
        }

    def resolve_embedding_cfg(self, file_cfg: "FileSettings") -> dict[str, Any]:
        """
        Merge embedding config from file with env-controlled device selection.

        Precedence:
          1) TXNEWS_EMBEDDING_DEVICE (explicit)
          2) Always use cpu for embedding (override all other settings)
          3) config/config.yaml value
        """
        cfg: dict[str, Any] = dict(file_cfg.embedding or {})

        # Always use CPU for embedding, regardless of other settings
        # But respect explicit device setting if provided
        dev = (self.embedding_device or "").strip()
        if dev:
            cfg["device"] = dev
        else:
            cfg["device"] = "cpu"
        return cfg

    def load_file_settings(self) -> FileSettings:
        if not self.config_yaml.exists():
            return FileSettings()
        data = yaml.safe_load(self.config_yaml.read_text(encoding="utf-8")) or {}
        return FileSettings(**data)

    def load_sources(self) -> list[str]:
        if not self.sources_txt.exists():
            return []
        lines = [line.strip() for line in self.sources_txt.read_text(encoding="utf-8").splitlines()]
        return [line for line in lines if line and not line.startswith("#")]


def get_settings() -> Settings:
    return Settings()
