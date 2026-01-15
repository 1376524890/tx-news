# Input: domain ids（event_id/ts_code/src-dst relation）+ graph_env
# Output: stable node_id / edge_id and Qdrant point_id (env-isolated)
# Pos: v2 KG ID helpers（变更时同步更新以上注释与所属目录 FOLDER.md）

from __future__ import annotations


def _clean(s: str) -> str:
    return (s or "").strip()


def _require_env(graph_env: str) -> str:
    env = _clean(graph_env).lower()
    if env not in {"prod", "sandbox"}:
        raise ValueError(f"invalid graph_env: {graph_env}")
    return env


def node_id_event(event_id: str) -> str:
    eid = _clean(event_id)
    if not eid:
        raise ValueError("event_id is empty")
    return f"event:{eid}"


def node_id_ticker(ts_code: str) -> str:
    ts = _clean(ts_code).upper()
    if not ts:
        raise ValueError("ts_code is empty")
    return f"ticker:{ts}"


def edge_id(*, src: str, relation: str, dst: str) -> str:
    s = _clean(src)
    r = _clean(relation)
    d = _clean(dst)
    if not s or not r or not d:
        raise ValueError("edge_id parts are empty")
    return f"edge:{s}|{r}|{d}"


def point_id(*, graph_env: str, item_id: str) -> str:
    """
    Qdrant point ids must be unique; to keep prod/sandbox isolated while preserving stable domain ids,
    we prefix the env in the point id string (the domain id stays in payload fields).
    """
    env = _require_env(graph_env)
    item = _clean(item_id)
    if not item:
        raise ValueError("item_id is empty")
    return f"{env}::{item}"

