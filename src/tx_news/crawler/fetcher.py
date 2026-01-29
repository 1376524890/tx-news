# Input: URL 与抓取超时/重试配置
# Output: FetchResult（status/headers/body/checksum）与 root 链接列表（优先最新）
# Pos: HTTP 抓取与链接提取组件（变更时同步更新以上注释与所属目录 FOLDER.md）

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
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


_DATE_PATTERNS = (
    re.compile(r"(?P<y>20\d{2})[./-](?P<m>0?[1-9]|1[0-2])[./-](?P<d>0?[1-9]|[12]\d|3[01])"),
    re.compile(r"(?P<y>20\d{2})(?P<m>0[1-9]|1[0-2])(?P<d>0[1-9]|[12]\d|3[01])"),
)


def _extract_date_score(text: str, now: datetime) -> float:
    best: datetime | None = None
    for pattern in _DATE_PATTERNS:
        for m in pattern.finditer(text):
            try:
                y = int(m.group("y"))
                mo = int(m.group("m"))
                d = int(m.group("d"))
                dt = datetime(y, mo, d, tzinfo=timezone.utc)
            except Exception:
                continue
            if dt > now + timedelta(days=7):
                continue
            if best is None or dt > best:
                best = dt
    return best.timestamp() if best else 0.0


def extract_links(root_url: str, html: bytes, *, max_links: int = 100) -> list[str]:
    soup = BeautifulSoup(html, "lxml")
    base = urlparse(root_url)
    links: list[tuple[float, int, str]] = []
    seen: set[str] = set()
    now = datetime.now(timezone.utc)
    max_candidates = max(max_links * 5, max_links)
    order = 0
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
        order += 1
        text = a.get_text(strip=True) or ""
        score = max(_extract_date_score(u, now), _extract_date_score(text, now))
        links.append((score, order, u))
        if len(links) >= max_candidates:
            break
    if any(score > 0 for score, _, _ in links):
        links.sort(key=lambda item: (-item[0], item[1]))
    return [u for _, _, u in links[:max_links]]
