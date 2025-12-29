# Input: 环境变量（TXNEWS_*）+ config/config.yaml + config/sources.txt
# Output: Settings/FileSettings（包含 infra/模型/源列表等配置）
# Pos: 全局配置加载入口（变更时同步更新以上注释与所属目录 FOLDER.md）

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field
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

    # dashscope
    dashscope_api_key: str | None = None

    # local files
    config_dir: Path = Path("config")
    config_yaml: Path = Path("config/config.yaml")
    sources_txt: Path = Path("config/sources.txt")

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
