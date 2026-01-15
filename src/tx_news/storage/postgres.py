# Input: Postgres DSN（进程内缓存 Engine）与 ORM 模型
# Output: 建表与 CRUD/查询函数（articles/versions/analysis/signals/a_share 等）+（可选）从本地缓存引导主数据
# Pos: Postgres 数据访问层（变更时同步更新以上注释与所属目录 FOLDER.md）

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from functools import lru_cache
from threading import Lock

from sqlalchemy import create_engine, desc, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from tx_news.db import AShareBasic, Analysis, Article, ArticleVersion, Base, FeedbackLog, RawDoc, Signal, Source


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@lru_cache(maxsize=8)
def make_engine(pg_dsn: str) -> Engine:
    # IMPORTANT:
    # - This function is intentionally cached so API endpoints / Celery tasks do not create a new
    #   Engine (and a new connection pool) per request/task, which can exhaust Postgres connections
    #   during long-running tests.
    kwargs: dict[str, object] = {"pool_pre_ping": True}
    if not pg_dsn.startswith("sqlite"):
        kwargs.update(
            pool_size=5,
            max_overflow=10,
            pool_timeout=30,
            pool_recycle=1800,
        )
    return create_engine(pg_dsn, **kwargs)


_init_lock = Lock()
_initialized_urls: set[str] = set()


def init_db(engine: Engine) -> None:
    # Ensure create_all is executed only once per Engine URL in this process.
    url_key = str(engine.url)
    if url_key in _initialized_urls:
        return
    with _init_lock:
        if url_key in _initialized_urls:
            return
        Base.metadata.create_all(engine)
        _initialized_urls.add(url_key)


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


def insert_feedback(engine: Engine, *, uid: str, kind: str, data: dict) -> None:
    with session_scope(engine) as s:
        s.add(FeedbackLog(uid=str(uid or ""), kind=str(kind or ""), data=data or {}, created_at=utcnow()))


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


def get_a_shares(engine: Engine, ts_codes: list[str]) -> list[AShareBasic]:
    ts_codes = [str(x or "").strip() for x in (ts_codes or [])]
    ts_codes = [x for x in ts_codes if x]
    if not ts_codes:
        return []
    with session_scope(engine) as s:
        rows = s.scalars(select(AShareBasic).where(AShareBasic.ts_code.in_(ts_codes))).all()
        return list(rows)


def upsert_a_share_basic(engine: Engine, rows: list[dict]) -> None:
    if not rows:
        return
    with session_scope(engine) as s:
        # Fast path: empty table -> bulk insert without per-row SELECT.
        any_existing = s.scalar(select(AShareBasic.ts_code).limit(1))
        if not any_existing:
            now = utcnow()
            for r in rows:
                ts_code = str(r.get("ts_code") or "").strip()
                if not ts_code:
                    continue
                s.add(
                    AShareBasic(
                        ts_code=ts_code,
                        name=str(r.get("name") or ""),
                        area=r.get("area"),
                        industry=r.get("industry"),
                        market=r.get("market"),
                        list_date=r.get("list_date"),
                        aliases=r.get("aliases") or {"names": [str(r.get("name") or "")]},
                        created_at=now,
                        updated_at=now,
                    )
                )
            return

        ts_codes = [str(r.get("ts_code") or "").strip() for r in rows]
        ts_codes = [c for c in ts_codes if c]
        existing_rows = s.scalars(select(AShareBasic).where(AShareBasic.ts_code.in_(ts_codes))).all()
        existing_by_code = {r.ts_code: r for r in existing_rows}

        now = utcnow()
        for r in rows:
            ts_code = str(r.get("ts_code") or "").strip()
            if not ts_code:
                continue
            existing = existing_by_code.get(ts_code)
            if existing:
                existing.name = str(r.get("name") or existing.name or "")
                existing.area = r.get("area")
                existing.industry = r.get("industry")
                existing.market = r.get("market")
                existing.list_date = r.get("list_date")
                existing.aliases = r.get("aliases") or {"names": [existing.name]}
                existing.updated_at = now
            else:
                s.add(
                    AShareBasic(
                        ts_code=ts_code,
                        name=str(r.get("name") or ""),
                        area=r.get("area"),
                        industry=r.get("industry"),
                        market=r.get("market"),
                        list_date=r.get("list_date"),
                        aliases=r.get("aliases") or {"names": [str(r.get("name") or "")]},
                        created_at=now,
                        updated_at=now,
                    )
                )


def load_a_share_name_map(engine: Engine) -> dict[str, str]:
    with session_scope(engine) as s:
        rows = s.execute(select(AShareBasic.ts_code, AShareBasic.name)).all()
    # name -> ts_code
    return {name: ts_code for ts_code, name in rows if name and ts_code}


def a_share_basic_has_any(engine: Engine) -> bool:
    with session_scope(engine) as s:
        return s.scalar(select(AShareBasic.ts_code).limit(1)) is not None


def bootstrap_a_share_basic_from_cache(engine: Engine) -> dict[str, object]:
    """
    Best-effort bootstrap for first-run deployments:
    - If `a_share_basic` is empty, try loading `var/cache/a_share/stock_basic.json` and upsert into Postgres.
    - Never performs network requests (call maintenance task for that).
    """
    if a_share_basic_has_any(engine):
        return {"status": "skipped_not_empty"}

    try:
        from tx_news.integrations.tushare_sync import TushareSync
    except Exception as e:  # pragma: no cover
        return {"status": "failed", "reason": f"import_tushare_sync_failed: {e}"}

    rows = (TushareSync(token="").load_cached_stock_basic() or [])[:]
    if not rows:
        return {"status": "no_cache"}

    try:
        upsert_a_share_basic(engine, rows)
        return {"status": "loaded_cache", "rows": len(rows)}
    except IntegrityError:
        # Concurrent bootstrap attempts can race; treat as OK and let caller re-read.
        return {"status": "concurrent_conflict", "rows": len(rows)}
