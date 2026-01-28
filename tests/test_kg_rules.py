# Input: v2 KG rule helpers
# Output: deterministic ID/text/scoring behavior
# Pos: unit tests for src/tx_news/kg (no external services)

from __future__ import annotations

from tx_news.kg.ids import edge_id, node_id_event, node_id_ticker, point_id
from tx_news.kg.scoring import related_to_reason_and_weight
from tx_news.kg.snapshot import build_event_snapshot_text


def test_ids() -> None:
    assert node_id_event("e1") == "event:e1"
    assert node_id_ticker("000001.SZ") == "ticker:000001.SZ"
    assert edge_id(src="event:e1", relation="related_to", dst="event:e2") == "edge:event:e1|related_to|event:e2"
    assert point_id(graph_env="prod", item_id="event:e1") == "prod::event:e1"


def test_snapshot_text_is_capped() -> None:
    articles = [
        {"title": "t" * 500, "url": "http://example.com/a", "published_at": "2025-01-01T00:00:00+00:00"},
        {"title": "x" * 500, "url": "http://example.com/b", "published_at": "2025-01-01T00:01:00+00:00"},
        {"title": "y" * 500, "url": "http://example.com/c", "published_at": "2025-01-01T00:02:00+00:00"},
    ]
    s = build_event_snapshot_text(
        event_id="evt",
        event_type="policy",
        tickers=["000001.SZ", "600519.SH"],
        articles=articles,
        impact={"direction": "up", "confidence": 0.6},
        max_chars=2000,
    )
    assert 0 < len(s) <= 2000
    assert "EventID: evt" in s


def test_related_to_scoring() -> None:
    w, reason = related_to_reason_and_weight(
        event_type_a="policy",
        tickers_a=["000001.SZ", "600519.SH"],
        event_type_b="policy",
        tickers_b=["000001.SZ"],
    )
    assert 0.0 < w <= 1.0
    assert "shared_tickers" in reason
