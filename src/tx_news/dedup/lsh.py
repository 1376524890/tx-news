from __future__ import annotations

import os
import pickle
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from datasketch import MinHash, MinHashLSH


def tokenize(text: str) -> list[str]:
    # simple tokenizer for Chinese+English: keep CJK chars and alnum, split by whitespace/punct
    text = re.sub(r"\s+", " ", text)
    tokens = re.findall(r"[\u4e00-\u9fff]|[a-zA-Z0-9]+", text)
    return tokens


def make_minhash(tokens: Iterable[str], *, num_perm: int = 128) -> MinHash:
    mh = MinHash(num_perm=num_perm)
    for t in tokens:
        mh.update(t.encode("utf-8"))
    return mh


@dataclass
class LshIndex:
    path: Path
    threshold: float = 0.85
    num_perm: int = 128

    def load(self) -> MinHashLSH:
        if self.path.exists():
            with self.path.open("rb") as f:
                return pickle.load(f)
        return MinHashLSH(threshold=self.threshold, num_perm=self.num_perm)

    def save(self, lsh: MinHashLSH) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        with tmp.open("wb") as f:
            pickle.dump(lsh, f)
        os.replace(tmp, self.path)


@dataclass
class LshDeduper:
    index: LshIndex

    def find_duplicate(self, *, text: str) -> str | None:
        lsh = self.index.load()
        mh = make_minhash(tokenize(text), num_perm=self.index.num_perm)
        candidates = lsh.query(mh)
        return candidates[0] if candidates else None

    def insert(self, canonical_id: str, text: str) -> None:
        lsh = self.index.load()
        mh = make_minhash(tokenize(text), num_perm=self.index.num_perm)
        lsh.insert(canonical_id, mh)
        self.index.save(lsh)
