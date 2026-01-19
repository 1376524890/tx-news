# Input: 文本内容、canonical_id、时间戳与本地索引文件路径
# Output: 近重复候选 canonical_id 与可按时间窗口裁剪的 LSH 索引
# Pos: MinHash LSH 去重实现（变更时同步更新以上注释与所属目录 FOLDER.md）

from __future__ import annotations

import os
import pickle
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

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

    def _new_lsh(self) -> MinHashLSH:
        return MinHashLSH(threshold=self.threshold, num_perm=self.num_perm)

    def load_state(self, *, cutoff_ts: float | None = None) -> "LshState":
        lsh = self._new_lsh()
        entries: dict[str, dict[str, Any]] = {}
        if self.path.exists():
            try:
                with self.path.open("rb") as f:
                    obj = pickle.load(f)
            except Exception:
                obj = None

            if isinstance(obj, MinHashLSH):
                state = LshState(lsh=lsh, entries={})
                self.save_state(state.lsh, state.entries)
                return state

            if isinstance(obj, dict) and int(obj.get("version", 0)) >= 1:
                stored_num_perm = obj.get("num_perm")
                stored_threshold = obj.get("threshold")
                stored_entries = obj.get("entries") if isinstance(obj.get("entries"), dict) else {}
                stored_lsh = obj.get("lsh") if isinstance(obj.get("lsh"), MinHashLSH) else None

                if stored_num_perm is not None and int(stored_num_perm) != self.num_perm:
                    stored_entries = {}
                    stored_lsh = None
                if stored_threshold is not None and float(stored_threshold) != float(self.threshold):
                    stored_entries = {}
                    stored_lsh = None

                entries = dict(stored_entries)
                if entries and stored_lsh is not None:
                    lsh = stored_lsh
                elif entries:
                    lsh = self._new_lsh()
                    for cid, entry in entries.items():
                        mh = entry.get("mh") if isinstance(entry, dict) else None
                        if isinstance(mh, MinHash):
                            lsh.insert(cid, mh)

        if cutoff_ts is not None and entries:
            keep: dict[str, dict[str, Any]] = {}
            for cid, entry in entries.items():
                ts = _entry_ts(entry)
                if ts is not None and ts >= cutoff_ts:
                    keep[cid] = entry
            if len(keep) != len(entries):
                lsh = self._new_lsh()
                for cid, entry in keep.items():
                    mh = entry.get("mh") if isinstance(entry, dict) else None
                    if isinstance(mh, MinHash):
                        lsh.insert(cid, mh)
                entries = keep
                self.save_state(lsh, entries)

        if not entries:
            lsh = self._new_lsh()

        return LshState(lsh=lsh, entries=entries)

    def save_state(self, lsh: MinHashLSH, entries: dict[str, dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        with tmp.open("wb") as f:
            pickle.dump(
                {
                    "version": 1,
                    "num_perm": self.num_perm,
                    "threshold": self.threshold,
                    "lsh": lsh,
                    "entries": entries,
                },
                f,
            )
        os.replace(tmp, self.path)


@dataclass
class LshState:
    lsh: MinHashLSH
    entries: dict[str, dict[str, Any]]


@dataclass
class LshDeduper:
    index: LshIndex

    def find_duplicate(self, *, text: str, cutoff_ts: float | None = None) -> str | None:
        state = self.index.load_state(cutoff_ts=cutoff_ts)
        mh = make_minhash(tokenize(text), num_perm=self.index.num_perm)
        candidates = state.lsh.query(mh)
        return candidates[0] if candidates else None

    def insert(
        self,
        canonical_id: str,
        text: str,
        *,
        timestamp: float,
        cutoff_ts: float | None = None,
    ) -> None:
        state = self.index.load_state(cutoff_ts=cutoff_ts)
        mh = make_minhash(tokenize(text), num_perm=self.index.num_perm)
        state.lsh.insert(canonical_id, mh)
        state.entries[canonical_id] = {"ts": float(timestamp), "mh": mh}
        self.index.save_state(state.lsh, state.entries)


def _entry_ts(entry: Any) -> float | None:
    if isinstance(entry, dict):
        ts = entry.get("ts")
        try:
            return float(ts)
        except (TypeError, ValueError):
            return None
    return None
