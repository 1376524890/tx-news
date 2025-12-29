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

    # local files
    config_dir: Path = Path("config")
    config_yaml: Path = Path("config/config.yaml")
    sources_txt: Path = Path("config/sources.txt")

    def resolve_llm(self, file_cfg: "FileSettings") -> dict[str, Any]:
        llm = file_cfg.llm or {}
        base_url = (
            (self.llm_base_url or "").strip()
            or str(llm.get("base_url") or "").strip()
            or "https://dashscope.aliyuncs.com/compatible-mode/v1"
        )
        model = (
            (self.llm_model_name or "").strip()
            or str(llm.get("model") or "").strip()
            or "qwen3-max"
        )
        api_key = (
            (self.llm_api_key or "").strip()
            or (self.dashscope_api_key or "").strip()
            or str(llm.get("api_key") or "").strip()
            or None
        )
        timeout_seconds = int(llm.get("timeout_seconds") or 60)
        provider = str(llm.get("provider") or "openai_compat")
        return {
            "provider": provider,
            "base_url": base_url,
            "model": model,
            "api_key": api_key,
            "timeout_seconds": timeout_seconds,
        }

    def load_file_settings(self) -> FileSettings:
        if not self.config_yaml.exists():
            return FileSettings()
        data = yaml.safe_load(self.config_yaml.read_text(encoding="utf-8")) or {}
        return FileSettings(**data)

    def load_sources(self) -> list[str]:
        if not self.sources_txt.exists():
            return []
        lines = [l.strip() for l in self.sources_txt.read_text(encoding="utf-8").splitlines()]
        return [l for l in lines if l and not l.startswith("#")]


def get_settings() -> Settings:
    return Settings()
