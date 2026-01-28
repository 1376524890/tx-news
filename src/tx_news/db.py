# Input: SQLAlchemy 声明式模型依赖与字段定义
# Output: Base/Source/RawDoc/Article/Analysis/Signal/AShareBasic 等 ORM 模型
# Pos: Postgres 数据模型定义（变更时同步更新以上注释与所属目录 FOLDER.md）

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_id: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    root_url: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RawDoc(Base):
    __tablename__ = "raw_documents"
    __table_args__ = (UniqueConstraint("url", name="uq_raw_url"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_id: Mapped[str] = mapped_column(String(200), nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status_code: Mapped[int] = mapped_column(Integer, nullable=False)
    headers: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    content_type: Mapped[str | None] = mapped_column(String(200), nullable=True)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    s3_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Article(Base):
    __tablename__ = "articles"
    __table_args__ = (UniqueConstraint("canonical_id", name="uq_article_canonical"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    canonical_id: Mapped[str] = mapped_column(String(64), nullable=False)

    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    lsh_signature: Mapped[str | None] = mapped_column(String(256), nullable=True)

    embedding_model: Mapped[str | None] = mapped_column(String(200), nullable=True)
    embedding_dim: Mapped[int | None] = mapped_column(Integer, nullable=True)
    embedding_ref: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    versions: Mapped[list["ArticleVersion"]] = relationship(back_populates="article")

    analysis: Mapped["Analysis | None"] = relationship(back_populates="article", uselist=False)


class ArticleVersion(Base):
    __tablename__ = "article_versions"
    __table_args__ = (UniqueConstraint("url", name="uq_version_url"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    canonical_id: Mapped[str] = mapped_column(String(64), ForeignKey("articles.canonical_id"), nullable=False)

    source_id: Mapped[str] = mapped_column(String(200), nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    raw_s3_key: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    article: Mapped[Article] = relationship(back_populates="versions")


class Analysis(Base):
    __tablename__ = "analyses"
    __table_args__ = (UniqueConstraint("canonical_id", name="uq_analysis_canonical"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    canonical_id: Mapped[str] = mapped_column(String(64), ForeignKey("articles.canonical_id"), nullable=False)

    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    llm_used: Mapped[bool] = mapped_column(Integer, nullable=False)  # 0/1 for portability

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    article: Mapped[Article] = relationship(back_populates="analysis")


class Signal(Base):
    __tablename__ = "signals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    canonical_id: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)  # breaking / alert / etc.
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AShareBasic(Base):
    __tablename__ = "a_share_basic"
    __table_args__ = (UniqueConstraint("ts_code", name="uq_a_share_ts_code"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts_code: Mapped[str] = mapped_column(String(32), nullable=False)  # e.g. 000001.SZ
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    area: Mapped[str | None] = mapped_column(String(64), nullable=True)
    industry: Mapped[str | None] = mapped_column(String(128), nullable=True)
    market: Mapped[str | None] = mapped_column(String(64), nullable=True)
    list_date: Mapped[str | None] = mapped_column(String(16), nullable=True)
    aliases: Mapped[dict] = mapped_column(JSONB, nullable=False)  # {"names": [...]} etc.
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class FeedbackLog(Base):
    __tablename__ = "feedback_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    uid: Mapped[str] = mapped_column(String(128), nullable=False)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)  # e.g. graph_node_click / thumbs_up
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class KGRun(Base):
    __tablename__ = "kg_runs"

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)  # uuid string
    graph_env: Mapped[str] = mapped_column(String(16), nullable=False)  # prod|sandbox
    trigger_canonical_id: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)  # running|committed|failed|rolled_back
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class KGOpsLog(Base):
    __tablename__ = "kg_ops_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(64), ForeignKey("kg_runs.run_id"), nullable=False)
    phase: Mapped[str] = mapped_column(String(32), nullable=False)  # planner|validator|critic|executor
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class KGSnapshot(Base):
    __tablename__ = "kg_snapshots"

    snapshot_id: Mapped[str] = mapped_column(String(64), primary_key=True)  # uuid string
    run_id: Mapped[str] = mapped_column(String(64), ForeignKey("kg_runs.run_id"), nullable=False)
    graph_env: Mapped[str] = mapped_column(String(16), nullable=False)
    snapshot_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
