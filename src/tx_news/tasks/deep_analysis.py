# Input: canonical 文章内容 + Qdrant 相似检索 + DashScope API（可选）
# Output: 深分析后的结构化结果写回 analyses，并发出 signal
# Pos: Deep Path 任务（变更时同步更新以上注释与所属目录 FOLDER.md）

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from tx_news.analysis.dashscope import DashScopeClient
from tx_news.analysis.rules import EventWindowPlanner
from tx_news.embedding.embedder import Embedder
from tx_news.settings import get_settings
from tx_news.storage.postgres import get_analysis, get_article, get_latest_version, init_db, make_engine, upsert_analysis, insert_signal
from tx_news.storage.qdrant import QdrantStore
from tx_news.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@celery_app.task(name="tx_news.tasks.deep_analysis.deep_optimize")
def deep_optimize(canonical: dict[str, Any]) -> dict[str, Any]:
    """
    Agentic-style deep analysis:
    - retrieve semantically related articles (vector search)
    - build a compact evidence bundle
    - ask LLM to produce a refined structured result
    - upsert DB result
    """
    settings = get_settings()
    file_cfg = settings.load_file_settings()
    api_key = settings.dashscope_api_key or ((file_cfg.llm or {}).get("api_key"))
    if not api_key:
        return {"skipped": True, "reason": "no_api_key"}

    engine = make_engine(settings.pg_dsn)
    init_db(engine)

    embedding_cfg = file_cfg.embedding or {}
    model_name = embedding_cfg.get("model_name", "BAAI/bge-small-zh-v1.5")
    embedder = Embedder(model_name_or_path=model_name)
    qdrant = QdrantStore(url=settings.qdrant_url, collection=settings.qdrant_collection)

    vector = embedder.embed(canonical["text"][:4000])
    related = qdrant.search(vector=vector, limit=8)

    evidence: list[dict[str, Any]] = []
    for p in related:
        cid = str(p.id)
        v = get_latest_version(engine, cid)
        a = get_article(engine, cid)
        if not a:
            continue
        evidence.append(
            {
                "canonical_id": cid,
                "score": float(p.score or 0.0),
                "title": a.title,
                "url": v.url if v else None,
                "published_at": v.published_at.isoformat() if v and v.published_at else None,
            }
        )

    current_analysis = get_analysis(engine, canonical["canonical_id"])
    planner = EventWindowPlanner(event_windows_minutes=file_cfg.event_windows_minutes or {})
    event_type = (current_analysis.event_type if current_analysis else "other")
    minutes = planner.window_minutes(event_type)

    client = DashScopeClient(api_key=api_key, model=(file_cfg.llm or {}).get("model", "qwen3-max"))
    system = (
        "你是金融新闻分析助手。请只输出一个 JSON 对象，不要输出任何多余文本。"
        "你需要在已有初步分析的基础上，结合相关证据进行二次推理与修正。"
        "输出字段：event_type, entities, tickers, impact, index_view, evidence。"
        "注意：不要输出新闻全文，不要输出长段引用。"
    )
    user = (
        f"目标新闻标题：{canonical.get('title') or ''}\n"
        f"目标新闻正文：{canonical.get('text','')[:5000]}\n\n"
        f"初步分析：{current_analysis.data if current_analysis else None}\n\n"
        f"相似新闻证据（TopN，含时间与链接）：{evidence}\n\n"
        f"请在 {minutes} 分钟的事件窗口假设下进行逻辑化分析，修正与补全结构化结果。"
    )

    out = client.chat_json(system=system, user=user)
    result = {
        **(current_analysis.data if current_analysis else {}),
        **out,
        "canonical_id": canonical["canonical_id"],
        "deep_optimized_at": utcnow().isoformat(),
    }
    upsert_analysis(engine, canonical["canonical_id"], event_type=str(result.get("event_type", event_type)), data=result, llm_used=True)
    insert_signal(engine, canonical["canonical_id"], "deep_analysis_updated", {"event_type": result.get("event_type", event_type)})
    return {"updated": True}
