# Input: URL 与抓取超时/重试配置
# Output: FetchResult（status/headers/body/checksum）与 root 链接列表
# Pos: HTTP 抓取与链接提取组件（变更时同步更新以上注释与所属目录 FOLDER.md）

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class FetchResult:
    url: str
    fetched_at: datetime
    status_code: int
    headers: dict[str, str]
    content_type: str | None
    body: bytes
    checksum: str


@dataclass(frozen=True)
class Fetcher:
    timeout_seconds: int = 20
    max_retries: int = 3

    def fetch(self, url: str) -> FetchResult:
        headers = {"User-Agent": "tx-news/0.1 (+https://example.invalid)"}
        last_exc: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                with httpx.Client(timeout=self.timeout_seconds, follow_redirects=True) as client:
                    r = client.get(url, headers=headers)
                body = r.content or b""
                ct = r.headers.get("content-type")
                return FetchResult(
                    url=str(r.url),
                    fetched_at=utcnow(),
                    status_code=r.status_code,
                    headers={k.lower(): v for k, v in r.headers.items()},
                    content_type=ct,
                    body=body,
                    checksum=sha256_hex(body),
                )
            except Exception as e:
                last_exc = e
                logger.warning("fetch failed attempt=%s url=%s err=%s", attempt, url, e)
        assert last_exc is not None
        raise last_exc


def extract_links(root_url: str, html: bytes, *, max_links: int = 100) -> list[str]:
    soup = BeautifulSoup(html, "lxml")
    base = urlparse(root_url)
    links: list[str] = []
    seen: set[str] = set()
    for a in soup.find_all("a", href=True):
        href = a.get("href")
        if not href:
            continue
        u = urljoin(root_url, href)
        pu = urlparse(u)
        if pu.scheme not in ("http", "https"):
            continue
        if pu.netloc != base.netloc:
            continue
        # naive heuristic: avoid obvious non-article pages
        if any(x in pu.path.lower() for x in ("/video", "/photo", "/login", "/register")):
            continue
        u = u.split("#", 1)[0]
        if u in seen:
            continue
        seen.add(u)
        links.append(u)
        if len(links) >= max_links:
            break
    return links
