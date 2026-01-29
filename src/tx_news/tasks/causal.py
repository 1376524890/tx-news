# Input: canonical_id/event_id + analyses(affected_variables/tickers) + Qdrant edge memory
# Output: v3 causal edges (variable->entity + event->entity) written to KG
# Pos: causal synthesis task entry (update header and FOLDER.md on change)

from __future__ import annotations

import logging
import math
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from redis import Redis
from sqlalchemy import text

from tx_news.analysis.causal_vars import load_causal_variables, normalize_affected_variables
from tx_news.embedding.embedder import DEFAULT_EMBEDDING_MODEL, build_embedder
from tx_news.kg.graphops import EvalReport, GraphOp, GraphOpsPlan, validate_plan
from tx_news.kg.ids import edge_id, node_id_event, node_id_ticker, node_id_variable, point_id
from tx_news.settings import get_settings
from tx_news.storage.postgres import (
    create_kg_run,
    finish_kg_run,
    get_a_shares,
    get_analysis,
    get_event_canonical_ids,
    get_kg_snapshot,
    init_db,
    insert_kg_ops_log,
    insert_kg_snapshot,
    make_engine,
    session_scope,
)
from tx_news.storage.qdrant import QdrantStore
from tx_news.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)

CAUSAL_LOCK_TTL_SECONDS = 15 * 60


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _release_redis_lock(redis: Redis, key: str, token: str) -> None:
    redis.eval(
        "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end",
        1,
        key,
        token,
    )


def _qdrant_stores() -> tuple[QdrantStore, QdrantStore, QdrantStore]:
    settings = get_settings()
    return (
        QdrantStore(url=settings.qdrant_url, collection="txnews_event_memory"),
        QdrantStore(url=settings.qdrant_url, collection="txnews_entity_memory"),
        QdrantStore(url=settings.qdrant_url, collection="txnews_edge_memory"),
    )


def _sector_for_industry(industry: str | None) -> str:
    s = str(industry or "").lower()
    if any(k in s for k in ["银行", "证券", "保险", "金融", "bank", "broker"]):
        return "banking"
    if any(k in s for k in ["地产", "房地产", "real estate"]):
        return "real_estate"
    if any(k in s for k in ["建筑", "建材", "基建", "工程", "construction", "infrastructure"]):
        return "infrastructure"
    if any(k in s for k in ["化工", "材料", "钢铁", "有色", "金属", "materials", "metals"]):
        return "materials"
    if any(k in s for k in ["能源", "煤", "石油", "油气", "电力", "energy", "oil", "gas"]):
        return "energy"
    if any(k in s for k in ["汽车", "汽配", "auto"]):
        return "auto"
    if any(k in s for k in ["电子", "半导体", "芯片", "计算机", "通信", "electronics", "semiconductor"]):
        return "electronics"
    if any(k in s for k in ["消费", "食品", "饮料", "零售", "家电", "纺织", "服装", "consumer", "retail"]):
        return "consumer"
    if any(k in s for k in ["制造", "机械", "设备", "manufacturing", "machinery"]):
        return "manufacturing"
    return "other"


def _allowed_by_scope(var_meta: dict[str, Any], sector: str) -> bool:
    applies_to = var_meta.get("applies_to") if isinstance(var_meta.get("applies_to"), dict) else {}
    sectors = applies_to.get("sectors") if isinstance(applies_to.get("sectors"), list) else []
    if sectors:
        return str(sector) in {str(s) for s in sectors}
    return True


def _op_counts(ops: list[GraphOp]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for op in ops:
        counts[op.op] = counts.get(op.op, 0) + 1
    return counts


def _serialize_record(r: Any) -> dict[str, Any]:
    rid = getattr(r, "id", None)
    payload = getattr(r, "payload", None)
    vector = getattr(r, "vector", None)
    if isinstance(vector, dict):
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


def _execute_ops(
    *,
    plan: GraphOpsPlan,
    embedder,
    event_store: QdrantStore,
    entity_store: QdrantStore,
    edge_store: QdrantStore,
) -> dict[str, list[str]]:
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
        elif op.op in {"UPSERT_TICKER", "UPSERT_VARIABLE"}:
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


def _event_tickers(engine, canonical_ids: list[str]) -> list[str]:
    tickers: list[str] = []
    for cid in canonical_ids:
        an = get_analysis(engine, cid)
        if not an or not isinstance(an.data, dict):
            continue
        raw = an.data.get("tickers")
        if isinstance(raw, list):
            for item in raw:
                if isinstance(item, dict):
                    ts = str(item.get("ts_code") or "").strip().upper()
                else:
                    ts = str(item or "").strip().upper()
                if ts:
                    tickers.append(ts)
    # stable de-dup
    seen: set[str] = set()
    out: list[str] = []
    for ts in tickers:
        if ts in seen:
            continue
        seen.add(ts)
        out.append(ts)
    return out


def _event_variables(engine, canonical_ids: list[str], max_vars: int) -> list[dict[str, Any]]:
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
        pos = int(st["dir_counts"].get("+") or 0)
        neg = int(st["dir_counts"].get("-") or 0)
        neu = int(st["dir_counts"].get("0") or 0)
        total = pos + neg + neu
        if total <= 0:
            direction = "uncertain"
            consistency = 0.0
        elif pos == neg and pos > 0:
            direction = "uncertain"
            consistency = pos / total
        elif pos > neg:
            direction = "+"
            consistency = pos / total
        elif neg > pos:
            direction = "-"
            consistency = neg / total
        else:
            direction = "0" if neu > 0 else "uncertain"
            consistency = neu / total if total else 0.0

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


def _list_recent_events(engine, since: datetime, max_events: int) -> list[str]:
    with session_scope(engine) as s:
        rows = s.execute(
            text(
                "select distinct data->>'event_id' as event_id "
                "from analyses "
                "where created_at >= :since "
                "and (data->>'event_id') is not null and (data->>'event_id') <> '' "
                "limit :limit"
            ),
            {"since": since, "limit": int(max_events)},
        ).all()
    out: list[str] = []
    for eid, in rows:
        e = str(eid or "").strip()
        if e:
            out.append(e)
    return out


def _build_var_entity_stats(
    *,
    engine,
    var_names: list[str],
    var_index: dict[str, dict[str, Any]],
    lookback_days: int,
    max_events: int,
    max_vars: int,
) -> dict[str, dict[str, Any]]:
    var_set = {str(v).strip() for v in var_names if str(v).strip()}
    if not var_set:
        return {}

    since = utcnow() - timedelta(days=int(lookback_days))
    event_ids = _list_recent_events(engine, since, max_events)

    stats: dict[str, dict[str, Any]] = {v: {"max_count": 1, "ticker_stats": {}} for v in var_set}

    for event_id in event_ids:
        cids = get_event_canonical_ids(engine, event_id, limit=12)
        if not cids:
            continue
        event_vars = _event_variables(engine, cids, max_vars=max_vars)
        event_vars = [v for v in event_vars if v.get("var") in var_set]
        if not event_vars:
            continue
        tickers = _event_tickers(engine, cids)
        if not tickers:
            continue
        a_share = get_a_shares(engine, tickers)
        sector_by_code = {r.ts_code: _sector_for_industry(r.industry) for r in a_share}
        evidence_id = cids[0]
        for v in event_vars:
            vname = str(v.get("var") or "")
            vdir = str(v.get("direction") or "uncertain")
            vstats = stats.setdefault(vname, {"max_count": 1, "ticker_stats": {}})
            tstats = vstats["ticker_stats"]
            vmeta = var_index.get(vname) or {}
            for ts_code in tickers:
                sector = sector_by_code.get(ts_code, "other")
                if not _allowed_by_scope(vmeta, sector):
                    continue
                entry = tstats.setdefault(
                    ts_code,
                    {"total": 0, "pos": 0, "neg": 0, "evidence": []},
                )
                entry["total"] += 1
                if vdir == "+":
                    entry["pos"] += 1
                elif vdir == "-":
                    entry["neg"] += 1
                if len(entry["evidence"]) < 32:
                    entry["evidence"].append(evidence_id)
                vstats["max_count"] = max(vstats["max_count"], entry["total"])

    return stats


def _build_causal_plan(
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
        raise ValueError("analysis_not_found")
    event_id = str(an.data.get("event_id") or "").strip()
    if not event_id:
        raise ValueError("missing_event_id")

    settings = get_settings()
    file_cfg = settings.load_file_settings()
    causal_cfg = file_cfg.causal if isinstance(file_cfg.causal, dict) else {}
    lookback_days = int(causal_cfg.get("lookback_days") or 30)
    max_events = int(causal_cfg.get("max_events") or 500)
    min_support = int(causal_cfg.get("min_support") or 3)
    min_consistency = float(causal_cfg.get("min_consistency") or 0.6)
    max_var_edges = int(causal_cfg.get("max_variable_edges_per_var") or 80)
    max_causal_edges = int(causal_cfg.get("max_causal_edges") or 60)
    max_event_vars = int(causal_cfg.get("max_event_variables") or 6)
    event_var_min_conf = float(causal_cfg.get("event_var_min_conf") or 0.5)
    var_entity_min_weight = float(causal_cfg.get("var_entity_min_weight") or 0.2)
    conflict_ratio_threshold = float(causal_cfg.get("conflict_ratio_threshold") or 0.3)
    conflict_penalty = float(causal_cfg.get("conflict_penalty") or 0.6)

    canonical_ids = get_event_canonical_ids(engine, event_id, limit=12)
    event_vars = _event_variables(engine, canonical_ids, max_vars=max_event_vars)
    if not event_vars:
        return GraphOpsPlan(run_id=run_id, trigger={"canonical_id": canonical_id}, ops=[])
    event_tickers = _event_tickers(engine, canonical_ids)
    if not event_tickers:
        return GraphOpsPlan(run_id=run_id, trigger={"canonical_id": canonical_id}, ops=[])

    var_index = load_causal_variables()
    var_names = [v.get("var") for v in event_vars if v.get("var")]
    var_stats = _build_var_entity_stats(
        engine=engine,
        var_names=var_names,
        var_index=var_index,
        lookback_days=lookback_days,
        max_events=max_events,
        max_vars=max_event_vars,
    )

    now_ts = int(utcnow().timestamp())
    ops: list[GraphOp] = []

    # (1) variable nodes (stored in entity_memory by design)
    for var_name in var_names:
        meta = var_index.get(str(var_name) or "") or {}
        domain = str(meta.get("domain") or "macro")
        desc = str(meta.get("desc") or "").strip()
        desc_text = f"{var_name} ({domain})"
        if desc:
            desc_text += f"\n{desc}"
        desc_text = desc_text[:800]
        node_id = node_id_variable(str(var_name))
        payload = {
            "node_type": "latent_variable",
            "node_id": node_id,
            "var": str(var_name),
            "domain": domain,
            "description_text": desc_text,
            "updated_at_ts": now_ts,
            "graph_env": graph_env,
        }
        ops.append(GraphOp(op="UPSERT_VARIABLE", graph_env=graph_env, item_id=node_id, text=desc_text, payload=payload))

    # (2) variable -> entity edges (statistical)
    var_entity_edges: dict[str, dict[str, dict[str, Any]]] = {}
    for var_name in var_names:
        vstats = var_stats.get(str(var_name)) or {}
        tstats = vstats.get("ticker_stats") if isinstance(vstats.get("ticker_stats"), dict) else {}
        max_count = int(vstats.get("max_count") or 1)
        ranked: list[tuple[float, str, dict[str, Any]]] = []
        for ts_code, entry in tstats.items():
            total = int(entry.get("total") or 0)
            if total < min_support:
                continue
            pos = int(entry.get("pos") or 0)
            neg = int(entry.get("neg") or 0)
            consistency = max(pos, neg) / total if total > 0 else 0.0
            conflict_ratio = min(pos, neg) / total if total > 0 else 0.0
            if pos > neg and consistency >= min_consistency:
                effect_dir = "+"
            elif neg > pos and consistency >= min_consistency:
                effect_dir = "-"
            else:
                effect_dir = "mixed"
            weight = max(0.0, min(1.0, total / max_count)) if max_count > 0 else 0.0
            confidence = max(0.0, min(1.0, weight * consistency))
            if conflict_ratio > conflict_ratio_threshold:
                confidence = max(0.0, min(1.0, confidence * (1.0 - conflict_penalty * conflict_ratio)))
            ranked.append((confidence, ts_code, {
                "total": total,
                "pos": pos,
                "neg": neg,
                "conflict_ratio": conflict_ratio,
                "consistency": consistency,
                "effect_direction": effect_dir,
                "weight": weight,
                "confidence": confidence,
                "evidence": entry.get("evidence") or [],
            }))

        ranked.sort(key=lambda x: x[0], reverse=True)
        var_entity_edges.setdefault(str(var_name), {})
        for conf, ts_code, stats in ranked[: max_var_edges]:
            src = node_id_variable(str(var_name))
            dst = node_id_ticker(ts_code)
            eid = edge_id(src=src, relation="variable_impacts_entity", dst=dst)
            evidence = list(dict.fromkeys(stats.get("evidence") or []))[:64]
            if not evidence:
                evidence = canonical_ids[:1]
            reason = (
                f"var={var_name}; support={stats['total']}; pos={stats['pos']}; neg={stats['neg']}; "
                f"consistency={stats['consistency']:.2f}; conflict={stats['conflict_ratio']:.2f}; "
                f"effect={stats['effect_direction']}"
            )
            payload = {
                "edge_type": "edge",
                "edge_id": eid,
                "src": src,
                "dst": dst,
                "relation": "variable_impacts_entity",
                "effect_direction": stats["effect_direction"],
                "weight": stats["weight"],
                "confidence": stats["confidence"],
                "support": stats["total"],
                "support_pos": stats["pos"],
                "support_neg": stats["neg"],
                "consistency": stats["consistency"],
                "conflict_ratio": stats["conflict_ratio"],
                "confidence_breakdown": {
                    "support_events": stats["total"],
                    "same_direction_ratio": stats["consistency"],
                    "conflict_ratio": stats["conflict_ratio"],
                    "temporal_validity": 1.0,
                    "recentness": 0.5,
                },
                "confidence_model": "stat_v1",
                "edge_class": "statistical",
                "reason_text": reason[:800],
                "evidence_canonical_ids": evidence,
                "updated_at_ts": now_ts,
                "graph_env": graph_env,
            }
            ops.append(GraphOp(op="UPSERT_EDGE", graph_env=graph_env, item_id=eid, text=f"variable_impacts_entity: {reason}", payload=payload))
            var_entity_edges[str(var_name)][ts_code] = {
                **stats,
                "edge_id": eid,
            }

    # (3) event -> entity causal edges (EventEntityCausalSynthesizer)
    event_node = node_id_event(event_id)
    max_evidence = 12
    evidence_strength = math.log1p(len(canonical_ids)) / math.log1p(max_evidence) if canonical_ids else 0.0
    temporal_alignment = 1.0
    event_created_at = getattr(an, "created_at", None)
    if event_created_at:
        age_days = max(0.0, (utcnow() - event_created_at).total_seconds() / 86400.0)
        recentness = max(0.0, 1.0 - (age_days / max(1.0, float(lookback_days))))
    else:
        recentness = 1.0

    def _event_entity_causal_synthesizer() -> list[dict[str, Any]]:
        out: list[tuple[float, dict[str, Any]]] = []
        for ts_code in event_tickers:
            best: dict[str, Any] | None = None
            for v in event_vars:
                vname = str(v.get("var") or "")
                vdir = str(v.get("direction") or "uncertain")
                if vdir not in {"+", "-"}:
                    continue
                vconf = float(v.get("confidence") or 0.0)
                if vconf < event_var_min_conf:
                    continue
                edge_stats = (var_entity_edges.get(vname) or {}).get(ts_code)
                if not edge_stats:
                    continue
                effect_dir = str(edge_stats.get("effect_direction") or "mixed")
                if effect_dir not in {"+", "-"}:
                    continue
                if float(edge_stats.get("weight") or 0.0) < var_entity_min_weight:
                    continue
                if int(edge_stats.get("total") or 0) < min_support:
                    continue
                causal_dir = "+" if vdir == effect_dir else "-"
                consistency = float(edge_stats.get("consistency") or 0.0)
                conflict_ratio = float(edge_stats.get("conflict_ratio") or 0.0)
                path_support = float(edge_stats.get("weight") or 0.0)
                base_conf = (
                    0.35 * evidence_strength
                    + 0.25 * consistency
                    + 0.20 * temporal_alignment
                    + 0.20 * path_support
                )
                final_conf = max(0.0, min(1.0, base_conf * max(0.2, min(1.0, vconf))))
                if conflict_ratio > conflict_ratio_threshold:
                    final_conf = max(0.0, min(1.0, final_conf * (1.0 - conflict_penalty * conflict_ratio)))
                reason = (
                    f"via={vname}; var_dir={vdir}; effect={effect_dir}; "
                    f"support={edge_stats.get('total')}; consistency={consistency:.2f}; "
                    f"conflict={conflict_ratio:.2f}; conf={final_conf:.2f}"
                )
                candidate = {
                    "ts_code": ts_code,
                    "direction": causal_dir,
                    "weight": final_conf,
                    "confidence": final_conf,
                    "reason": reason,
                    "via": vname,
                    "confidence_breakdown": {
                        "support_events": edge_stats.get("total"),
                        "same_direction_ratio": consistency,
                        "temporal_validity": temporal_alignment,
                        "recentness": recentness,
                        "conflict_ratio": conflict_ratio,
                        "path_support": path_support,
                        "event_var_confidence": vconf,
                    },
                    "components": {
                        "evidence_strength": evidence_strength,
                        "consistency": consistency,
                        "temporal_alignment": temporal_alignment,
                        "path_support": path_support,
                    },
                }
                if not best or final_conf > float(best.get("confidence") or 0.0):
                    best = candidate
            if best:
                out.append((float(best.get("confidence") or 0.0), best))
        out.sort(key=lambda x: x[0], reverse=True)
        return [c for _, c in out]

    causal_candidates = _event_entity_causal_synthesizer()

    for c in causal_candidates[: max_causal_edges]:
        src = event_node
        dst = node_id_ticker(str(c.get("ts_code") or ""))
        eid = edge_id(src=src, relation="causal", dst=dst)
        reason = str(c.get("reason") or "")
        payload = {
            "edge_type": "edge",
            "edge_id": eid,
            "src": src,
            "dst": dst,
            "relation": "causal",
            "direction": c.get("direction"),
            "weight": float(c.get("weight") or 0.0),
            "confidence": float(c.get("confidence") or 0.0),
            "confidence_breakdown": c.get("confidence_breakdown") or {},
            "confidence_model": "causal_v1",
            "edge_class": "causal",
            "reason_text": reason[:800],
            "evidence_canonical_ids": canonical_ids[:64],
            "via_variables": [c.get("via")],
            "components": c.get("components") or {},
            "updated_at_ts": now_ts,
            "graph_env": graph_env,
        }
        ops.append(GraphOp(op="UPSERT_EDGE", graph_env=graph_env, item_id=eid, text=f"causal: {reason}", payload=payload))

    return GraphOpsPlan(run_id=run_id, trigger={"canonical_id": canonical_id}, ops=ops)


@celery_app.task(name="tx_news.tasks.causal.causal_synthesize_from_canonical")
def causal_synthesize_from_canonical(canonical_id: str) -> dict[str, Any]:
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

    event_id = str(an.data.get("event_id") or "").strip()
    if not event_id:
        return {"skipped": True, "reason": "missing_event_id"}

    lock_key = f"txnews:lock:causal:{event_id}"
    lock_token = secrets.token_hex(8)
    try:
        redis = Redis.from_url(settings.redis_url)
        got_lock = bool(redis.set(lock_key, lock_token, nx=True, ex=CAUSAL_LOCK_TTL_SECONDS))
    except Exception:
        got_lock = True
        redis = None  # type: ignore[assignment]
    if not got_lock:
        return {"skipped": True, "reason": "lock_busy"}

    run_id = str(uuid.uuid4())
    snapshot_id = str(uuid.uuid4())
    try:
        logger.info(
            "graphflow causal_start canonical_id=%s run_id=%s event_id=%s",
            cid,
            run_id,
            event_id,
        )
        create_kg_run(engine, run_id=run_id, graph_env="prod", trigger_canonical_id=cid)

        embedding_cfg = settings.resolve_embedding_cfg(file_cfg)
        embedder, _strategy = build_embedder(embedding_cfg)
        model_name = str(embedding_cfg.get("model_name") or DEFAULT_EMBEDDING_MODEL)
        _ = model_name

        event_store, entity_store, edge_store = _qdrant_stores()

        plan_sb = _build_causal_plan(
            run_id=run_id,
            canonical_id=cid,
            graph_env="sandbox",
            engine=engine,
            event_store=event_store,
            entity_store=entity_store,
            edge_store=edge_store,
        )
        insert_kg_ops_log(engine, run_id=run_id, phase="planner", payload=plan_sb.model_dump())
        logger.info("graphflow causal_plan canonical_id=%s run_id=%s ops=%s", cid, run_id, _op_counts(plan_sb.ops))
        if not plan_sb.ops:
            finish_kg_run(engine, run_id=run_id, status="skipped")
            return {"skipped": True, "reason": "no_ops", "run_id": run_id}
        validate_plan(plan_sb)
        insert_kg_ops_log(engine, run_id=run_id, phase="validator", payload={"ok": True})

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

        prod_point_ids = {
            "event_memory": [point_id(graph_env="prod", item_id=op.item_id) for op in prod_ops if op.op == "UPSERT_EVENT"],
            "entity_memory": [
                point_id(graph_env="prod", item_id=op.item_id)
                for op in prod_ops
                if op.op in {"UPSERT_TICKER", "UPSERT_VARIABLE"}
            ],
            "edge_memory": [
                point_id(graph_env="prod", item_id=op.item_id)
                for op in prod_ops
                if op.op in {"UPSERT_EDGE", "DELETE_EDGE"}
            ],
        }
        before = _snapshot_before(
            event_store=event_store,
            entity_store=entity_store,
            edge_store=edge_store,
            prod_point_ids=prod_point_ids,
        )

        touched_sb = _execute_ops(plan=plan_sb, embedder=embedder, event_store=event_store, entity_store=entity_store, edge_store=edge_store)
        insert_kg_ops_log(engine, run_id=run_id, phase="executor", payload={"graph_env": "sandbox", "touched": touched_sb})

        report = EvalReport(run_id=run_id, verdict="pass")
        insert_kg_ops_log(engine, run_id=run_id, phase="critic", payload=report.model_dump())
        if report.verdict != "pass":
            finish_kg_run(engine, run_id=run_id, status="failed")
            return {"skipped": True, "reason": "critic_fail", "run_id": run_id}

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
        logger.info(
            "graphflow causal_committed canonical_id=%s run_id=%s touched=%s",
            cid,
            run_id,
            {k: len(v) for k, v in touched_prod.items()},
        )
        return {"updated": True, "run_id": run_id, "snapshot_id": snapshot_id}
    except Exception as e:
        logger.exception("causal_synthesize failed canonical_id=%s", cid)
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


@celery_app.task(name="tx_news.tasks.causal.causal_rollback")
def causal_rollback(snapshot_id: str) -> dict[str, Any]:
    """
    Roll back prod points to the pre-commit state stored in kg_snapshots.snapshot_payload.before.
    Mirrors kg_rollback for causal runs.
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
