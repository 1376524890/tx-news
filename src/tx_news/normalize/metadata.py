# Input: HTML bytes（meta/time 标签等）
# Output: 尽力解析得到的 published_at（datetime|None）
# Pos: 发布时间提取器（变更时同步更新以上注释与所属目录 FOLDER.md）

from __future__ import annotations

from datetime import datetime

from bs4 import BeautifulSoup
from dateutil import parser as dateparser


def try_parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return dateparser.parse(value)
    except Exception:
        return None


def extract_published_at(html: bytes) -> datetime | None:
    soup = BeautifulSoup(html, "lxml")
    # common meta tags
    for key in (
        ("property", "article:published_time"),
        ("name", "pubdate"),
        ("name", "publishdate"),
        ("name", "publish_date"),
        ("name", "date"),
    ):
        tag = soup.find("meta", attrs={key[0]: key[1]})
        if tag and tag.get("content"):
            dt = try_parse_datetime(tag.get("content"))
            if dt:
                return dt
    # <time datetime=...>
    time_tag = soup.find("time")
    if time_tag:
        dt = try_parse_datetime(time_tag.get("datetime") or time_tag.get_text(strip=True))
        if dt:
            return dt
    return None
