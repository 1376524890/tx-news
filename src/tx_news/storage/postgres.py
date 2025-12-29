# Input: Postgres DSN/Engine 与 ORM 模型
# Output: 建表与 CRUD/查询函数（articles/versions/analysis/signals/a_share 等）
# Pos: Postgres 数据访问层（变更时同步更新以上注释与所属目录 FOLDER.md）

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone

from sqlalchemy import create_engine, desc, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from tx_news.db import AShareBasic, Analysis, Article, ArticleVersion, Base, RawDoc, Signal, Source


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def make_engine(pg_dsn: str) -> Engine:
    return create_engine(pg_dsn, pool_pre_ping=True)


def init_db(engine: Engine) -> None:
    Base.metadata.create_all(engine)


@contextmanager
def session_scope(engine: Engine):
    # Keep loaded ORM attributes accessible after commit/close (used by API/tool layers).
    session = Session(engine, expire_on_commit=False)
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def ensure_sources(engine: Engine, root_urls: list[str]) -> None:
    with session_scope(engine) as s:
        existing = {row[0] for row in s.execute(select(Source.root_url)).all()}
        for root_url in root_urls:
            if root_url in existing:
                continue
            source_id = root_url.replace("https://", "").replace("http://", "").strip("/").lower()
            s.add(Source(source_id=source_id, root_url=root_url, created_at=utcnow()))


def upsert_raw(engine: Engine, *, source_id: str, url: str, fetched_at: datetime, status_code: int,
              headers: dict, content_type: str | None, checksum: str, s3_key: str | None) -> int | None:
    with session_scope(engine) as s:
        existing = s.scalar(select(RawDoc).where(RawDoc.url == url))
        if existing:
            return None
        doc = RawDoc(
            source_id=source_id,
            url=url,
            fetched_at=fetched_at,
            status_code=status_code,
            headers=headers,
            content_type=content_type,
            checksum=checksum,
            s3_key=s3_key,
            created_at=utcnow(),
        )
        s.add(doc)
        s.flush()
        return doc.id


def existing_raw_urls(engine: Engine, urls: list[str]) -> set[str]:
    if not urls:
        return set()
    with session_scope(engine) as s:
        rows = s.execute(select(RawDoc.url).where(RawDoc.url.in_(urls))).all()
    return {r[0] for r in rows}


def upsert_article(engine: Engine, article: Article) -> None:
    with session_scope(engine) as s:
        existing = s.scalar(select(Article).where(Article.canonical_id == article.canonical_id))
        if existing:
            existing.title = article.title
            existing.text = article.text
            existing.checksum = article.checksum
            existing.lsh_signature = article.lsh_signature
            existing.embedding_model = article.embedding_model
            existing.embedding_dim = article.embedding_dim
            existing.embedding_ref = article.embedding_ref
            existing.updated_at = utcnow()
            return
        s.add(article)


def insert_version(
    engine: Engine,
    *,
    canonical_id: str,
    source_id: str,
    url: str,
    fetched_at: datetime,
    published_at: datetime | None,
    checksum: str,
    raw_s3_key: str | None,
) -> None:
    with session_scope(engine) as s:
        existing = s.scalar(select(ArticleVersion).where(ArticleVersion.url == url))
        if existing:
            return
        s.add(
            ArticleVersion(
                canonical_id=canonical_id,
                source_id=source_id,
                url=url,
                fetched_at=fetched_at,
                published_at=published_at,
                checksum=checksum,
                raw_s3_key=raw_s3_key,
                created_at=utcnow(),
            )
        )


def upsert_analysis(engine: Engine, canonical_id: str, event_type: str, data: dict, llm_used: bool) -> None:
    with session_scope(engine) as s:
        existing = s.scalar(select(Analysis).where(Analysis.canonical_id == canonical_id))
        if existing:
            existing.event_type = event_type
            existing.data = data
            existing.llm_used = 1 if llm_used else 0
            existing.created_at = utcnow()
            return
        s.add(
            Analysis(
                canonical_id=canonical_id,
                event_type=event_type,
                data=data,
                llm_used=1 if llm_used else 0,
                created_at=utcnow(),
            )
        )


def insert_signal(engine: Engine, canonical_id: str, kind: str, data: dict) -> None:
    with session_scope(engine) as s:
        s.add(Signal(canonical_id=canonical_id, kind=kind, data=data, created_at=utcnow()))


def get_article(engine: Engine, canonical_id: str) -> Article | None:
    with session_scope(engine) as s:
        return s.scalar(select(Article).where(Article.canonical_id == canonical_id))


def get_articles(engine: Engine, canonical_ids: list[str]) -> list[Article]:
    if not canonical_ids:
        return []
    with session_scope(engine) as s:
        rows = s.scalars(select(Article).where(Article.canonical_id.in_(canonical_ids))).all()
        return list(rows)


def get_latest_version(engine: Engine, canonical_id: str) -> ArticleVersion | None:
    with session_scope(engine) as s:
        return s.scalar(
            select(ArticleVersion)
            .where(ArticleVersion.canonical_id == canonical_id)
            .order_by(desc(ArticleVersion.published_at), desc(ArticleVersion.fetched_at))
            .limit(1)
        )


def get_analysis(engine: Engine, canonical_id: str) -> Analysis | None:
    with session_scope(engine) as s:
        return s.scalar(select(Analysis).where(Analysis.canonical_id == canonical_id))


def list_signals(engine: Engine, limit: int = 50) -> list[Signal]:
    with session_scope(engine) as s:
        rows = s.scalars(select(Signal).order_by(desc(Signal.created_at)).limit(limit)).all()
        return list(rows)


def get_event_canonical_ids(engine: Engine, event_id: str, limit: int = 200) -> list[str]:
    with session_scope(engine) as s:
        rows = s.execute(
            select(Analysis.canonical_id)
            .where(Analysis.data["event_id"].astext == event_id)  # type: ignore[attr-defined]
            .order_by(desc(Analysis.created_at))
            .limit(limit)
        ).all()
    return [r[0] for r in rows]


def get_a_share(engine: Engine, ts_code: str):
    with session_scope(engine) as s:
        return s.scalar(select(AShareBasic).where(AShareBasic.ts_code == ts_code))


def upsert_a_share_basic(engine: Engine, rows: list[dict]) -> None:
    with session_scope(engine) as s:
        for r in rows:
            ts_code = r["ts_code"]
            existing = s.scalar(select(AShareBasic).where(AShareBasic.ts_code == ts_code))
            if existing:
                existing.name = r.get("name") or existing.name
                existing.area = r.get("area")
                existing.industry = r.get("industry")
                existing.market = r.get("market")
                existing.list_date = r.get("list_date")
                existing.aliases = r.get("aliases") or {"names": [existing.name]}
                existing.updated_at = utcnow()
                continue
            s.add(
                AShareBasic(
                    ts_code=ts_code,
                    name=r.get("name") or "",
                    area=r.get("area"),
                    industry=r.get("industry"),
                    market=r.get("market"),
                    list_date=r.get("list_date"),
                    aliases=r.get("aliases") or {"names": [r.get("name") or ""]},
                    created_at=utcnow(),
                    updated_at=utcnow(),
                )
            )


def load_a_share_name_map(engine: Engine) -> dict[str, str]:
    with session_scope(engine) as s:
        rows = s.execute(select(AShareBasic.ts_code, AShareBasic.name)).all()
    # name -> ts_code
    return {name: ts_code for ts_code, name in rows if name and ts_code}
