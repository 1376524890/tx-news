from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class RawDocument(BaseModel):
    source_id: str
    url: str
    fetched_at: datetime
    status_code: int
    headers: dict[str, str] = Field(default_factory=dict)
    content_type: str | None = None
    raw_bytes_s3_key: str | None = None
    raw_text: str | None = None
    checksum: str


class NormalizedArticle(BaseModel):
    source_id: str
    url: str
    fetched_at: datetime
    published_at: datetime | None = None
    title: str | None = None
    text: str
    lang: str = "zh"
    checksum: str
    raw_bytes_s3_key: str | None = None


class CanonicalArticle(BaseModel):
    canonical_id: str
    source_id: str
    url: str
    fetched_at: datetime
    published_at: datetime | None = None
    title: str | None = None
    text: str
    checksum: str
    duplicate_of: str | None = None
    lsh_signature: str | None = None
    embedding_model: str | None = None
    embedding_dim: int | None = None
    embedding_ref: str | None = None


class AnalysisResult(BaseModel):
    canonical_id: str
    event_type: str
    entities: list[dict[str, Any]] = Field(default_factory=list)
    tickers: list[dict[str, Any]] = Field(default_factory=list)
    impact: dict[str, Any] = Field(default_factory=dict)
    index_view: dict[str, Any] = Field(default_factory=dict)
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    llm_used: bool = False
    created_at: datetime

