from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from bs4 import BeautifulSoup
from readability import Document


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ReadabilityExtractor:
    def extract(self, html: bytes) -> dict[str, Any]:
        text_html = html.decode("utf-8", errors="ignore")
        doc = Document(text_html)
        title = (doc.short_title() or "").strip() or None
        content_html = doc.summary(html_partial=True)
        soup = BeautifulSoup(content_html, "lxml")
        text = soup.get_text("\n", strip=True)
        text = "\n".join([line.strip() for line in text.splitlines() if line.strip()])
        return {
            "title": title,
            "text": text,
            "checksum": sha256_text(text),
        }
