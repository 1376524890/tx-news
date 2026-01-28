# Input: rule-based KG deltas (nodes/edges) or LLM-produced plan
# Output: GraphOpsPlan/EvalReport schema + minimal validator
# Pos: v2 KG GraphOps schema（变更时同步更新以上注释与所属目录 FOLDER.md）

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


GraphEnv = Literal["prod", "sandbox"]
OpType = Literal["UPSERT_EVENT", "UPSERT_TICKER", "UPSERT_EDGE", "DELETE_EDGE"]
CriticVerdict = Literal["pass", "fail"]
EdgeVerdict = Literal["KEEP", "WEAKEN", "REMOVE"]


class GraphOp(BaseModel):
    op: OpType
    graph_env: GraphEnv
    # For nodes/edges we always use domain IDs (node_id/edge_id) in the plan;
    # executor converts to Qdrant point ids (env-prefixed).
    item_id: str = Field(min_length=1)
    # Text used to build the embedding vector in the executor (LLM plans never provide vectors).
    text: str | None = None
    vector: list[float] | None = None
    payload: dict[str, Any] | None = None


class GraphOpsPlan(BaseModel):
    run_id: str = Field(min_length=1)
    trigger: dict[str, Any]
    ops: list[GraphOp]


class EdgeReview(BaseModel):
    edge_id: str = Field(min_length=1)
    verdict: EdgeVerdict
    confidence: float = Field(ge=0.0, le=1.0)
    reasons: list[str] = Field(default_factory=list)


class EvalReport(BaseModel):
    run_id: str = Field(min_length=1)
    verdict: CriticVerdict
    requires_human_review: bool = False
    edge_reviews: list[EdgeReview] = Field(default_factory=list)
    global_risks: list[str] = Field(default_factory=list)


def validate_plan(plan: GraphOpsPlan) -> None:
    """
    Hard validation: reject anything outside our executable subset and enforce evidence/shape rules.
    """
    if not plan.ops:
        raise ValueError("GraphOpsPlan.ops is empty")
    for op in plan.ops:
        if op.op in {"UPSERT_EVENT", "UPSERT_TICKER"}:
            if not (isinstance(op.text, str) and op.text.strip()) and not (isinstance(op.vector, list) and op.vector):
                raise ValueError(f"{op.op} missing embedding input (text/vector)")
            if not isinstance(op.payload, dict):
                raise ValueError(f"{op.op} missing payload")
            if op.op == "UPSERT_EVENT" and not str(op.payload.get("snapshot_text") or "").strip():
                raise ValueError("UPSERT_EVENT payload.snapshot_text is empty")
            if op.op == "UPSERT_TICKER" and not str(op.payload.get("description_text") or "").strip():
                raise ValueError("UPSERT_TICKER payload.description_text is empty")
        if op.op == "UPSERT_EDGE":
            if not (isinstance(op.text, str) and op.text.strip()) and not (isinstance(op.vector, list) and op.vector):
                raise ValueError("UPSERT_EDGE missing embedding input (text/vector)")
            if not isinstance(op.payload, dict):
                raise ValueError("UPSERT_EDGE missing payload")
            ev = op.payload.get("evidence_canonical_ids")
            if not isinstance(ev, list) or not ev:
                raise ValueError("UPSERT_EDGE evidence_canonical_ids must be non-empty list")
            rt = str(op.payload.get("reason_text") or "").strip()
            if not rt:
                raise ValueError("UPSERT_EDGE reason_text is empty")
