# Input: canonical_id（来自 v1 analyze/deep_analysis）+ Postgres(articles/analyses/versions) + Qdrant(event/entity/edge memory)
# Output: v2/v3 KG 增量更新（sandbox->prod），审计与快照，周期治理任务
# Pos: KG 任务入口（变更时同步更新以上注释与所属目录 FOLDER.md）

from __future__ import annotations

import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from redis import Redis

from tx_news.analysis.causal_vars import load_causal_variables, normalize_affected_variables
from tx_news.embedding.embedder import DEFAULT_EMBEDDING_MODEL, build_embedder
from tx_news.kg.graphops import EvalReport, GraphOp, GraphOpsPlan, validate_plan
from tx_news.kg.ids import edge_id, node_id_event, node_id_ticker, node_id_variable, point_id
from tx_news.kg.scoring import related_to_reason_and_weight
from tx_news.kg.snapshot import build_event_snapshot_text
from tx_news.settings import get_settings
from tx_news.storage.postgres import (
    create_kg_run,
    finish_kg_run,
    get_a_shares,
    get_analysis,
    get_article,
    get_event_canonical_ids,
    get_latest_version,
    get_kg_snapshot,
    init_db,
    insert_kg_ops_log,
    insert_kg_snapshot,
    make_engine,
    session_scope,
)
from tx_news.storage.qdrant import QdrantStore
from tx_news.tasks.celery_app import celery_app

from sqlalchemy import text

logger = logging.getLogger(__name__)

KG_LOCK_TTL_SECONDS = 10 * 60


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _release_redis_lock(redis: Redis, key: str, token: str) -> None:
    redis.eval(
        "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end",
        1,
        key,
        token,
    )


def _normalize_tickers(value: Any) -> list[str]:
    out: list[str] = []
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                ts = str(item.get("ts_code") or "").strip().upper()
                if ts:
                    out.append(ts)
            elif isinstance(item, str):
                ts = item.strip().upper()
                if ts:
                    out.append(ts)
    # stable de-dup order
    seen: set[str] = set()
    uniq: list[str] = []
    for ts in out:
        if ts in seen:
            continue
        seen.add(ts)
        uniq.append(ts)
    return uniq


def _pick_direction(counts: dict[str, int]) -> tuple[str, float]:
    pos = int(counts.get("+") or 0)
    neg = int(counts.get("-") or 0)
    neu = int(counts.get("0") or 0)
    total = pos + neg + neu
    if total <= 0:
        return "uncertain", 0.0
    if pos == neg and pos > 0:
        return "uncertain", pos / total
    if pos > neg:
        return "+", pos / total
    if neg > pos:
        return "-", neg / total
    if neu > 0:
        return "0", neu / total
    return "uncertain", 0.0


def _aggregate_event_variables(
    engine,
    canonical_ids: list[str],
    *,
    max_vars: int,
) -> list[dict[str, Any]]:
    var_index = load_causal_variables()
    stats: dict[str, dict[str, Any]] = {}
    for cid in canonical_ids:
        an = get_analysis(engine, cid)
        if not an or not isinstance(an.data, dict):
            continue
        raw = an.data.get("affected_variables")
        vars_norm = normalize_affected_variables(raw, var_index=var_index, max_items=max_vars)
        for v in vars_norm:
            name = str(v.get("var") or "").strip()
            if not name:
                continue
            st = stats.setdefault(
                name,
                {
                    "dir_counts": {"+": 0, "-": 0, "0": 0},
                    "conf_sum": 0.0,
                    "conf_n": 0,
                    "evidence": [],
                },
            )
            direction = str(v.get("direction") or "uncertain")
            if direction in st["dir_counts"]:
                st["dir_counts"][direction] += 1
            st["conf_sum"] += float(v.get("confidence") or 0.0)
            st["conf_n"] += 1
            if len(st["evidence"]) < 64:
                st["evidence"].append(cid)

    out: list[dict[str, Any]] = []
    for name, st in stats.items():
        direction, consistency = _pick_direction(st.get("dir_counts") or {})
        conf_n = int(st.get("conf_n") or 0)
        conf_avg = (float(st.get("conf_sum") or 0.0) / conf_n) if conf_n > 0 else 0.0
        evidence = list(dict.fromkeys(st.get("evidence") or []))[:64]
        out.append(
            {
                "var": name,
                "direction": direction,
                "consistency": consistency,
                "confidence": max(0.0, min(1.0, conf_avg)),
                "evidence": evidence,
            }
        )
    out.sort(key=lambda x: float(x.get("confidence") or 0.0), reverse=True)
    return out[: int(max_vars)]


def _qdrant_stores() -> tuple[QdrantStore, QdrantStore, QdrantStore]:
    settings = get_settings()
    # v2 fixed base names (collections are created on demand).
    return (
        QdrantStore(url=settings.qdrant_url, collection="txnews_event_memory"),
        QdrantStore(url=settings.qdrant_url, collection="txnews_entity_memory"),
        QdrantStore(url=settings.qdrant_url, collection="txnews_edge_memory"),
    )


def _serialize_record(r: Any) -> dict[str, Any]:
    # qdrant-client returns Record with .id/.payload/.vector (vector may be dict for named vectors).
    rid = getattr(r, "id", None)
    payload = getattr(r, "payload", None)
    vector = getattr(r, "vector", None)
    if isinstance(vector, dict):
        # take the first vector if named vectors are used
        vector = next(iter(vector.values()), None)
    return {
        "id": str(rid) if rid is not None else None,
        "payload": payload if isinstance(payload, dict) else None,
        "vector": vector if isinstance(vector, list) else None,
    }


def _snapshot_before(
    *,
    event_store: QdrantStore,
    entity_store: QdrantStore,
    edge_store: QdrantStore,
    prod_point_ids: dict[str, list[str]],
) -> dict[str, Any]:
    before: dict[str, Any] = {"event_memory": [], "entity_memory": [], "edge_memory": []}

    for k, store in [
        ("event_memory", event_store),
        ("entity_memory", entity_store),
        ("edge_memory", edge_store),
    ]:
        ids = prod_point_ids.get(k) or []
        if not ids:
            continue
        try:
            recs = store.retrieve(point_ids=ids, with_payload=True, with_vectors=True)
            # Map by payload canonical_id (which we set to point_id string in upsert) when available.
            by_payload_id: dict[str, Any] = {}
            for r in recs:
                payload = getattr(r, "payload", None)
                if isinstance(payload, dict):
                    pid = payload.get("canonical_id")
                    if isinstance(pid, str) and pid:
                        by_payload_id[pid] = r
            for pid in ids:
                r = by_payload_id.get(pid)
                if not r:
                    before[k].append({"point_id": pid, "missing": True})
                else:
                    before[k].append({"point_id": pid, **_serialize_record(r)})
        except Exception as e:
            before[k].append({"error": str(e), "point_ids": ids[:50]})

    return before


def _build_rule_plan(
    *,
    run_id: str,
    canonical_id: str,
    graph_env: str,
    engine,
    event_store: QdrantStore,
    entity_store: QdrantStore,
    edge_store: QdrantStore,
) -> GraphOpsPlan:
    an = get_analysis(engine, canonical_id)
    if not an or not isinstance(an.data, dict):
        raise ValueError("analysis not_found")
    data = an.data

    event_id_raw = str(data.get("event_id") or "").strip()
    if not event_id_raw:
        raise ValueError("missing event_id in analysis")
    event_type = str(getattr(an, "event_type", None) or data.get("event_type") or "other").strip() or "other"

    # Evidence URL for this canonical.
    v = get_latest_version(engine, canonical_id)
    url = v.url if v else None

    tickers = _normalize_tickers(data.get("tickers"))
    a_share = get_a_shares(engine, tickers)
    name_by_code = {r.ts_code: r.name for r in a_share}
    industry_by_code = {r.ts_code: r.industry for r in a_share}

    ops: list[GraphOp] = []

    # (1) entity nodes
    now_ts = int(utcnow().timestamp())
    for ts_code in tickers:
        nid = node_id_ticker(ts_code)
        pid = point_id(graph_env=graph_env, item_id=nid)
        name = name_by_code.get(ts_code) or ""
        industry = industry_by_code.get(ts_code)
        desc = f"{ts_code} {name}".strip()
        if industry:
            desc += f"\nindustry={industry}"
        desc += f"\nrecent_event_id={event_id_raw}"
        if url:
            desc += f"\nrecent_url={url}"
        desc = desc[:1200]
        payload = {
            "node_type": "ticker",
            "node_id": nid,
            "ts_code": ts_code,
            "name": name,
            "industry": industry,
            "description_text": desc,
            "updated_at_ts": now_ts,
            "graph_env": graph_env,
        }
        ops.append(GraphOp(op="UPSERT_TICKER", graph_env=graph_env, item_id=nid, text=desc, payload=payload))

    # (2) event node snapshot
    event_node_id = node_id_event(event_id_raw)
    event_pid = point_id(graph_env=graph_env, item_id=event_node_id)

    canonical_ids = get_event_canonical_ids(engine, event_id_raw, limit=12)
    articles: list[dict[str, Any]] = []
    last_pub_ts = 0
    for cid in canonical_ids:
        a = get_article(engine, cid)
        v2 = get_latest_version(engine, cid)
        if not a:
            continue
        pdt = (v2.published_at or v2.fetched_at) if v2 else None
        if pdt:
            last_pub_ts = max(last_pub_ts, int(pdt.timestamp()))
        articles.append(
            {
                "canonical_id": cid,
                "title": a.title,
                "url": v2.url if v2 else None,
                "published_at": pdt.isoformat() if pdt else None,
                "created_at": getattr(an, "created_at", None).isoformat() if getattr(an, "created_at", None) else None,
            }
        )

    snapshot_text = build_event_snapshot_text(
        event_id=event_id_raw,
        event_type=event_type,
        tickers=tickers,
        articles=articles,
        impact=(data.get("impact") if isinstance(data.get("impact"), dict) else None),
        max_chars=2000,
    )
    event_payload = {
        "node_type": "event",
        "node_id": event_node_id,
        "event_id": event_id_raw,
        "event_type": event_type,
        "snapshot_text": snapshot_text,
        "canonical_ids": canonical_ids[:64],
        "last_published_at_ts": last_pub_ts,
        "updated_at_ts": now_ts,
        "graph_env": graph_env,
    }
    ops.append(GraphOp(op="UPSERT_EVENT", graph_env=graph_env, item_id=event_node_id, text=snapshot_text, payload=event_payload))

    # (2.5) event -> variable candidates (from affected_variables aggregation)
    settings = get_settings()
    file_cfg = settings.load_file_settings()
    causal_cfg = file_cfg.causal if isinstance(file_cfg.causal, dict) else {}
    max_vars = int(causal_cfg.get("max_event_variables") or 6)
    var_index = load_causal_variables()
    event_vars = _aggregate_event_variables(engine, canonical_ids, max_vars=max_vars)
    for vinfo in event_vars:
        var_name = str(vinfo.get("var") or "").strip()
        if not var_name:
            continue
        meta = var_index.get(var_name) or {}
        domain = str(meta.get("domain") or "macro")
        desc = str(meta.get("desc") or "").strip()
        desc_text = f"{var_name} ({domain})"
        if desc:
            desc_text += f"\n{desc}"
        desc_text = desc_text[:800]

        var_node_id = node_id_variable(var_name)
        var_payload = {
            "node_type": "latent_variable",
            "node_id": var_node_id,
            "var": var_name,
            "domain": domain,
            "description_text": desc_text,
            "updated_at_ts": now_ts,
            "graph_env": graph_env,
        }
        ops.append(GraphOp(op="UPSERT_VARIABLE", graph_env=graph_env, item_id=var_node_id, text=desc_text, payload=var_payload))

        direction = str(vinfo.get("direction") or "uncertain")
        confidence = float(vinfo.get("confidence") or 0.0)
        consistency = float(vinfo.get("consistency") or 0.0)
        evidence = vinfo.get("evidence") or canonical_ids[:1]
        if not isinstance(evidence, list) or not evidence:
            evidence = canonical_ids[:1]

        edge_key = edge_id(src=event_node_id, relation="event_impacts_variable", dst=var_node_id)
        reason = f"var={var_name}; dir={direction}; consistency={consistency:.2f}; conf={confidence:.2f}"
        payload = {
            "edge_type": "edge",
            "edge_id": edge_key,
            "src": event_node_id,
            "dst": var_node_id,
            "relation": "event_impacts_variable",
            "direction": direction,
            "weight": max(0.0, min(1.0, confidence)),
            "confidence": max(0.0, min(1.0, confidence)),
            "consistency": max(0.0, min(1.0, consistency)),
            "edge_class": "candidate",
            "reason_text": reason[:800],
            "evidence_canonical_ids": evidence[:64],
            "updated_at_ts": now_ts,
            "graph_env": graph_env,
        }
        ops.append(GraphOp(op="UPSERT_EDGE", graph_env=graph_env, item_id=edge_key, text=f"event_impacts_variable: {reason}", payload=payload))

    # (3) edges: mentions
    # frequency computed from last 12 analyses in this event
    ticker_counts: dict[str, int] = {t: 0 for t in tickers}
    evidence_by_ticker: dict[str, list[str]] = {t: [] for t in tickers}
    for cid in canonical_ids:
        an2 = get_analysis(engine, cid)
        if not an2 or not isinstance(an2.data, dict):
            continue
        for t in _normalize_tickers(an2.data.get("tickers")):
            if t in ticker_counts:
                ticker_counts[t] += 1
                if len(evidence_by_ticker[t]) < 64:
                    evidence_by_ticker[t].append(cid)
    max_count = max(ticker_counts.values()) if ticker_counts else 1

    for ts_code, cnt in ticker_counts.items():
        if cnt <= 0:
            continue
        src = event_node_id
        dst = node_id_ticker(ts_code)
        eid = edge_id(src=src, relation="mentions", dst=dst)
        reason = f"{ts_code} mentioned in {cnt}/{len(canonical_ids) or 1} evidence items."
        reason = reason[:800]
        w = float(cnt) / float(max_count or 1)
        edge_text = f"mentions: {reason}"
        payload = {
            "edge_type": "edge",
            "edge_id": eid,
            "src": src,
            "dst": dst,
            "relation": "mentions",
            "weight": max(0.0, min(1.0, w)),
            "confidence": 0.7,
            "edge_class": "correlation",
            "reason_text": reason,
            "evidence_canonical_ids": evidence_by_ticker.get(ts_code) or canonical_ids[:1],
            "updated_at_ts": now_ts,
            "graph_env": graph_env,
        }
        ops.append(GraphOp(op="UPSERT_EDGE", graph_env=graph_env, item_id=eid, text=edge_text, payload=payload))

    # (4) edges: related_to
    # Candidate events are other events in a +/- window around this analysis timestamp.
    # We use the event window minutes configured for this event_type.
    minutes = int((file_cfg.event_windows_minutes or {}).get(event_type, 180))
    anchor = getattr(an, "created_at", None) or utcnow()
    start = anchor - timedelta(minutes=minutes)
    end = anchor + timedelta(minutes=minutes)
    with session_scope(engine) as s:
        rows = s.execute(
            text(
                # event_id is stored inside analyses.data
                # NOTE: keep SQL simple; we filter time range by analyses.created_at.
                "select distinct data->>'event_id' as event_id, event_type "
                "from analyses "
                "where created_at between :start and :end "
                "and (data->>'event_id') is not null and (data->>'event_id') <> '' "
                "limit 2000"
            ),
            {"start": start, "end": end},
        ).all()
    candidates: list[tuple[str, str]] = []
    for eid2, et2 in rows:
        eid2s = str(eid2 or "").strip()
        if not eid2s or eid2s == event_id_raw:
            continue
        candidates.append((eid2s, str(et2 or "other")))

    scored: list[tuple[float, str, str]] = []  # (weight, neighbor_event_id, reason)
    for eid2s, et2 in candidates:
        cids2 = get_event_canonical_ids(engine, eid2s, limit=12)
        tks2: list[str] = []
        for cid in cids2[:6]:
            an3 = get_analysis(engine, cid)
            if an3 and isinstance(an3.data, dict):
                tks2.extend(_normalize_tickers(an3.data.get("tickers")))
        w2, reason2 = related_to_reason_and_weight(
            event_type_a=event_type,
            tickers_a=tickers,
            event_type_b=et2,
            tickers_b=tks2,
        )
        if w2 <= 0:
            continue
        scored.append((w2, eid2s, reason2))

    scored.sort(key=lambda x: x[0], reverse=True)
    for w2, eid2s, reason2 in scored[:6]:
        src = event_node_id
        dst = node_id_event(eid2s)
        edge_key = edge_id(src=src, relation="related_to", dst=dst)
        evidence = list(dict.fromkeys((canonical_ids[:6] + get_event_canonical_ids(engine, eid2s, limit=6))[:64]))
        edge_text = f"related_to: {reason2}"
        payload = {
            "edge_type": "edge",
            "edge_id": edge_key,
            "src": src,
            "dst": dst,
            "relation": "related_to",
            "weight": float(w2),
            "confidence": 0.55,
            "edge_class": "correlation",
            "reason_text": reason2,
            "evidence_canonical_ids": evidence,
            "updated_at_ts": now_ts,
            "graph_env": graph_env,
        }
        ops.append(GraphOp(op="UPSERT_EDGE", graph_env=graph_env, item_id=edge_key, text=edge_text, payload=payload))

    return GraphOpsPlan(run_id=run_id, trigger={"canonical_id": canonical_id}, ops=ops)


def _execute_ops(
    *,
    plan: GraphOpsPlan,
    embedder,
    event_store: QdrantStore,
    entity_store: QdrantStore,
    edge_store: QdrantStore,
) -> dict[str, list[str]]:
    """
    Execute upserts/deletes in Qdrant. Returns touched point ids by collection key.
    """
    touched: dict[str, list[str]] = {"event_memory": [], "entity_memory": [], "edge_memory": []}
    for op in plan.ops:
        env = op.graph_env
        item = op.item_id
        pid = point_id(graph_env=env, item_id=item)
        vec = op.vector
        if not vec:
            text = str(op.text or "").strip()
            if not text:
                raise ValueError(f"missing embedding text for op={op.op} item_id={item}")
            vec = embedder.embed(text[:2000])
        if op.op == "UPSERT_EVENT":
            event_store.upsert(point_id=pid, vector=vec, payload=op.payload or {})
            touched["event_memory"].append(pid)
        elif op.op == "UPSERT_TICKER":
            entity_store.upsert(point_id=pid, vector=vec, payload=op.payload or {})
            touched["entity_memory"].append(pid)
        elif op.op == "UPSERT_VARIABLE":
            entity_store.upsert(point_id=pid, vector=vec, payload=op.payload or {})
            touched["entity_memory"].append(pid)
        elif op.op == "UPSERT_EDGE":
            edge_store.upsert(point_id=pid, vector=vec, payload=op.payload or {})
            touched["edge_memory"].append(pid)
        elif op.op == "DELETE_EDGE":
            edge_store.delete(point_ids=[pid])
            touched["edge_memory"].append(pid)
        else:
            raise ValueError(f"unsupported op: {op.op}")
    return touched


@celery_app.task(name="tx_news.tasks.kg.kg_update_from_canonical")
def kg_update_from_canonical(canonical_id: str) -> dict[str, Any]:
    settings = get_settings()
    file_cfg = settings.load_file_settings()
    engine = make_engine(settings.pg_dsn)
    init_db(engine)

    cid = str(canonical_id or "").strip()
    if not cid:
        return {"skipped": True, "reason": "missing_canonical_id"}

    an = get_analysis(engine, cid)
    if not an or not isinstance(an.data, dict):
        return {"skipped": True, "reason": "analysis_not_found"}

    lock_key = f"txnews:lock:kg_update:{cid}"
    lock_token = secrets.token_hex(8)
    try:
        redis = Redis.from_url(settings.redis_url)
        got_lock = bool(redis.set(lock_key, lock_token, nx=True, ex=KG_LOCK_TTL_SECONDS))
    except Exception:
        got_lock = True
        redis = None  # type: ignore[assignment]
    if not got_lock:
        return {"skipped": True, "reason": "lock_busy"}

    run_id = str(uuid.uuid4())
    snapshot_id = str(uuid.uuid4())
    try:
        create_kg_run(engine, run_id=run_id, graph_env="prod", trigger_canonical_id=cid)

        embedding_cfg = settings.resolve_embedding_cfg(file_cfg)
        embedder, _strategy = build_embedder(embedding_cfg)
        model_name = str(embedding_cfg.get("model_name") or DEFAULT_EMBEDDING_MODEL)
        _ = model_name  # model name reserved for future collection scoping; v2 uses fixed collections

        event_store, entity_store, edge_store = _qdrant_stores()

        # Rule plan writes to sandbox first.
        plan_sb = _build_rule_plan(
            run_id=run_id,
            canonical_id=cid,
            graph_env="sandbox",
            engine=engine,
            event_store=event_store,
            entity_store=entity_store,
            edge_store=edge_store,
        )
        insert_kg_ops_log(engine, run_id=run_id, phase="planner", payload=plan_sb.model_dump())
        validate_plan(plan_sb)
        insert_kg_ops_log(engine, run_id=run_id, phase="validator", payload={"ok": True})

        # Build equivalent prod plan by copying ops and flipping env field + payload.graph_env.
        prod_ops: list[GraphOp] = []
        for op in plan_sb.ops:
            payload = dict(op.payload or {})
            payload["graph_env"] = "prod"
            prod_ops.append(
                GraphOp(
                    op=op.op,
                    graph_env="prod",
                    item_id=op.item_id,
                    text=op.text,
                    vector=op.vector,
                    payload=payload,
                )
            )
        plan_prod = GraphOpsPlan(run_id=run_id, trigger=plan_sb.trigger, ops=prod_ops)

        # Snapshot "before" for prod points that will be touched.
        prod_point_ids = {
            "event_memory": [point_id(graph_env="prod", item_id=op.item_id) for op in prod_ops if op.op == "UPSERT_EVENT"],
            "entity_memory": [
                point_id(graph_env="prod", item_id=op.item_id) for op in prod_ops if op.op in {"UPSERT_TICKER", "UPSERT_VARIABLE"}
            ],
            "edge_memory": [point_id(graph_env="prod", item_id=op.item_id) for op in prod_ops if op.op in {"UPSERT_EDGE", "DELETE_EDGE"}],
        }
        before = _snapshot_before(
            event_store=event_store,
            entity_store=entity_store,
            edge_store=edge_store,
            prod_point_ids=prod_point_ids,
        )

        # Execute sandbox ops.
        touched_sb = _execute_ops(plan=plan_sb, embedder=embedder, event_store=event_store, entity_store=entity_store, edge_store=edge_store)
        insert_kg_ops_log(engine, run_id=run_id, phase="executor", payload={"graph_env": "sandbox", "touched": touched_sb})

        # Critic: deterministic pass for now (edges already validated).
        report = EvalReport(run_id=run_id, verdict="pass")
        insert_kg_ops_log(engine, run_id=run_id, phase="critic", payload=report.model_dump())
        if report.verdict != "pass":
            finish_kg_run(engine, run_id=run_id, status="failed")
            return {"skipped": True, "reason": "critic_fail", "run_id": run_id}

        # Commit prod ops.
        touched_prod = _execute_ops(plan=plan_prod, embedder=embedder, event_store=event_store, entity_store=entity_store, edge_store=edge_store)
        insert_kg_ops_log(engine, run_id=run_id, phase="executor", payload={"graph_env": "prod", "touched": touched_prod})

        insert_kg_snapshot(
            engine,
            snapshot_id=snapshot_id,
            run_id=run_id,
            graph_env="prod",
            snapshot_payload={"before": before, "touched_prod": touched_prod},
        )
        finish_kg_run(engine, run_id=run_id, status="committed")
        return {"updated": True, "run_id": run_id, "snapshot_id": snapshot_id}
    except Exception as e:
        logger.exception("kg_update_from_canonical failed canonical_id=%s", cid)
        try:
            finish_kg_run(engine, run_id=run_id, status="failed")
        except Exception:
            pass
        return {"skipped": True, "reason": "error", "error": str(e), "run_id": run_id}
    finally:
        if got_lock and redis is not None:
            try:
                _release_redis_lock(redis, lock_key, lock_token)
            except Exception:
                pass


@celery_app.task(name="tx_news.tasks.kg.kg_gc")
def kg_gc() -> dict[str, Any]:
    """
    Periodic garbage collection / decay for edge memory.
    Rules are fixed in docs/V2_NEWS_KG_IMPLEMENTATION.md.
    """
    settings = get_settings()
    _event_store, _entity_store, edge_store = _qdrant_stores()

    now = utcnow()
    cutoff_delete = int((now - timedelta(days=7)).timestamp())
    cutoff_decay = int((now - timedelta(days=30)).timestamp())

    deleted = 0
    decayed = 0

    # Helper to iterate all matching edges.
    def _iter_edges(relation: str):
        offset = None
        while True:
            try:
                recs, offset2 = edge_store.scroll(
                    filter_payload={"graph_env": "prod", "relation": relation},
                    limit=256,
                    with_payload=True,
                    with_vectors=True,
                    offset=offset,
                )
            except Exception:
                break
            for r in recs:
                yield r
            if not offset2:
                break
            offset = offset2

    # Delete old control edges.
    for rel in ("ignore", "retrieval_priority"):
        to_del: list[str] = []
        for r in _iter_edges(rel):
            payload = getattr(r, "payload", None)
            if not isinstance(payload, dict):
                continue
            updated = int(payload.get("updated_at_ts") or 0)
            if updated and updated < cutoff_delete:
                pid = str(payload.get("canonical_id") or "")
                if pid:
                    to_del.append(pid)
            if len(to_del) >= 128:
                edge_store.delete(point_ids=to_del)
                deleted += len(to_del)
                to_del = []
        if to_del:
            edge_store.delete(point_ids=to_del)
            deleted += len(to_del)

    # Decay old related_to/causal/statistical edges.
    for rel in ("related_to", "causal", "variable_impacts_entity"):
        for r in _iter_edges(rel):
            payload = getattr(r, "payload", None)
            if not isinstance(payload, dict):
                continue
            updated = int(payload.get("updated_at_ts") or 0)
            if not updated or updated >= cutoff_decay:
                continue

            w = float(payload.get("weight") or 0.0)
            payload["weight"] = max(0.0, min(1.0, w * 0.5))
            payload["updated_at_ts"] = int(now.timestamp())

            pid = str(payload.get("canonical_id") or "")
            if not pid:
                continue
            vec = getattr(r, "vector", None)
            if isinstance(vec, dict):
                vec = next(iter(vec.values()), None)
            if not isinstance(vec, list) or not vec:
                continue
            edge_store.upsert(point_id=pid, vector=vec, payload=payload)
            decayed += 1

    return {"ok": True, "deleted": deleted, "decayed": decayed}


@celery_app.task(name="tx_news.tasks.kg.kg_reconcile")
def kg_reconcile() -> dict[str, Any]:
    """
    Daily reconcile: rebuild snapshot_text for events touched in the last 24 hours.
    """
    settings = get_settings()
    file_cfg = settings.load_file_settings()
    engine = make_engine(settings.pg_dsn)
    init_db(engine)

    embedding_cfg = settings.resolve_embedding_cfg(file_cfg)
    embedder, _strategy = build_embedder(embedding_cfg)

    event_store, _entity_store, _edge_store = _qdrant_stores()

    since = utcnow() - timedelta(hours=24)
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "select distinct data->>'event_id' as event_id, coalesce(event_type,'other') as event_type "
                "from analyses "
                "where created_at >= :since "
                "and (data->>'event_id') is not null and (data->>'event_id') <> '' "
                "limit 5000"
            ),
            {"since": since},
        ).all()

    updated = 0
    now_ts = int(utcnow().timestamp())
    for eid, et in rows:
        event_id_raw = str(eid or "").strip()
        if not event_id_raw:
            continue
        event_type = str(et or "other")
        cids = get_event_canonical_ids(engine, event_id_raw, limit=12)
        tickers: list[str] = []
        articles: list[dict[str, Any]] = []
        last_pub_ts = 0
        for cid in cids:
            an = get_analysis(engine, cid)
            if an and isinstance(an.data, dict):
                tickers.extend(_normalize_tickers(an.data.get("tickers")))
            a = get_article(engine, cid)
            v = get_latest_version(engine, cid)
            if not a:
                continue
            pdt = (v.published_at or v.fetched_at) if v else None
            if pdt:
                last_pub_ts = max(last_pub_ts, int(pdt.timestamp()))
            articles.append(
                {
                    "canonical_id": cid,
                    "title": a.title,
                    "url": v.url if v else None,
                    "published_at": pdt.isoformat() if pdt else None,
                }
            )

        tickers = _normalize_tickers([{"ts_code": t} for t in tickers])
        snapshot_text = build_event_snapshot_text(
            event_id=event_id_raw,
            event_type=event_type,
            tickers=tickers,
            articles=articles,
            impact=None,
        )
        vec = embedder.embed(snapshot_text)
        nid = node_id_event(event_id_raw)
        for env in ("sandbox", "prod"):
            pid = point_id(graph_env=env, item_id=nid)
            payload = {
                "node_type": "event",
                "node_id": nid,
                "event_id": event_id_raw,
                "event_type": event_type,
                "snapshot_text": snapshot_text,
                "canonical_ids": cids[:64],
                "last_published_at_ts": last_pub_ts,
                "updated_at_ts": now_ts,
                "graph_env": env,
            }
            event_store.upsert(point_id=pid, vector=vec, payload=payload)
        updated += 1

    return {"ok": True, "updated_events": updated}


@celery_app.task(name="tx_news.tasks.kg.kg_rollback")
def kg_rollback(snapshot_id: str) -> dict[str, Any]:
    """
    Roll back prod points to the pre-commit state stored in kg_snapshots.snapshot_payload.before.
    This is an operational safety valve (no auth; intended for trusted operators).
    """
    settings = get_settings()
    engine = make_engine(settings.pg_dsn)
    init_db(engine)

    sid = str(snapshot_id or "").strip()
    if not sid:
        return {"ok": False, "error": "missing_snapshot_id"}

    snap = get_kg_snapshot(engine, snapshot_id=sid)
    if not snap or not isinstance(snap.snapshot_payload, dict):
        return {"ok": False, "error": "snapshot_not_found"}

    payload = snap.snapshot_payload
    before = payload.get("before") if isinstance(payload.get("before"), dict) else {}
    run_id = str(getattr(snap, "run_id", "") or "")

    event_store, entity_store, edge_store = _qdrant_stores()

    restored = 0
    deleted = 0

    def _apply(store: QdrantStore, entries: list[dict[str, Any]]):
        nonlocal restored, deleted
        for e in entries:
            if not isinstance(e, dict):
                continue
            pid = str(e.get("point_id") or "").strip()
            if not pid:
                continue
            if e.get("missing"):
                store.delete(point_ids=[pid])
                deleted += 1
                continue
            vec = e.get("vector")
            pl = e.get("payload")
            if not isinstance(vec, list) or not vec:
                continue
            if not isinstance(pl, dict):
                continue
            store.upsert(point_id=pid, vector=vec, payload=pl)
            restored += 1

    _apply(event_store, list(before.get("event_memory") or []))
    _apply(entity_store, list(before.get("entity_memory") or []))
    _apply(edge_store, list(before.get("edge_memory") or []))

    if run_id:
        try:
            finish_kg_run(engine, run_id=run_id, status="rolled_back")
            insert_kg_ops_log(engine, run_id=run_id, phase="executor", payload={"op": "rollback", "snapshot_id": sid})
        except Exception:
            pass

    return {"ok": True, "snapshot_id": sid, "run_id": run_id or None, "restored": restored, "deleted": deleted}
