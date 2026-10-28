# -*- coding: utf-8 -*-
"""风格 RAG：从本地评论库里挑同标签、高赞的句子做 few-shot。"""
from __future__ import annotations

import json
from typing import Iterable


def examples(store, tags: Iterable[str], limit: int = 8) -> list[str]:
    tagset = {str(t).lower() for t in tags if str(t).strip()}
    rows = store.query(
        """SELECT c.message AS message, c.like_count AS like_count, v.tags AS tags
           FROM comments c LEFT JOIN videos v ON c.bvid=v.bvid
           WHERE c.like_count>0 ORDER BY c.like_count DESC LIMIT 300"""
    )
    scored: list[tuple[float, str]] = []
    for r in rows:
        msg = str(r.get("message") or "").strip()
        if not (4 <= len(msg) <= 60):
            continue
        try:
            vt = {str(x).lower() for x in json.loads(r.get("tags") or "[]")}
        except (TypeError, ValueError):
            vt = set()
        overlap = len(tagset & vt) if tagset else 0
        score = overlap * 20 + min(int(r.get("like_count") or 0), 500) / 100.0
        scored.append((score, msg))
    scored.sort(key=lambda x: x[0], reverse=True)
    out, seen = [], set()
    for _, msg in scored:
        key = msg[:12]
        if key in seen:
            continue
        seen.add(key)
        out.append(msg)
        if len(out) >= limit:
            break
    return out
