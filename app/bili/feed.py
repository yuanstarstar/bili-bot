# -*- coding: utf-8 -*-
"""信息流：推荐 / 热门 / 排行 / 搜索。只读。"""
from __future__ import annotations

import re
import time
from typing import Optional

from .client import BiliClient
from .errors import BiliError

_DUR_RE = re.compile(r"^\s*(\d{1,2}):(\d{1,2})(?::(\d{1,2}))?\s*$")


def _duration(v) -> int:
    if isinstance(v, (int, float)):
        return int(v)
    m = _DUR_RE.match(str(v or ""))
    if not m:
        return 0
    a, b, c = m.group(1), m.group(2), m.group(3)
    if c is not None:
        return int(a) * 3600 + int(b) * 60 + int(c)
    return int(a) * 60 + int(b)


def _norm(it: dict) -> Optional[dict]:
    if not isinstance(it, dict):
        return None
    bvid = str(it.get("bvid") or "").strip()
    if not bvid:
        return None
    owner = it.get("owner") or {}
    stat = it.get("stat") or {}
    return {
        "bvid": bvid,
        "aid": int(it.get("aid") or it.get("id") or 0),
        "cid": int(it.get("cid") or 0),
        "title": str(it.get("title") or "").replace("<em class=\"keyword\">", "").replace("</em>", ""),
        "desc": str(it.get("desc") or ""),
        "pic": str(it.get("pic") or ""),
        "duration": _duration(it.get("duration")),
        "pubdate": int(it.get("pubdate") or it.get("pub_time") or 0),
        "owner": {"mid": int(owner.get("mid") or 0), "name": str(owner.get("name") or "")},
        "stat": {"view": int(stat.get("view") or it.get("play") or 0),
                 "like": int(stat.get("like") or 0),
                 "reply": int(stat.get("reply") or 0),
                 "danmaku": int(stat.get("danmaku") or 0)},
        "source": str(it.get("goto") or "video"),
    }


def _clean(items: list) -> list[dict]:
    out, seen = [], set()
    for it in items or []:
        n = _norm(it)
        if n and n["bvid"] not in seen:
            seen.add(n["bvid"])
            out.append(n)
    return out


def recommend(client: BiliClient, ps: int = 12, fresh_idx: int = 1,
              fresh_idx_1h: int = 1) -> list[dict]:
    data = client.api("/x/web-interface/wbi/index/top/feed/rcmd", {
        "ps": ps, "fresh_type": 3, "feed_version": "V8",
        "fresh_idx": fresh_idx, "fresh_idx_1h": fresh_idx_1h,
        "brush": 0, "homepage_ver": 1,
    }, wbi=True)
    return _clean(data.get("item") or [])


def popular(client: BiliClient, ps: int = 20, pn: int = 1) -> list[dict]:
    data = client.api("/x/web-interface/popular", {"ps": ps, "pn": pn})
    return _clean(data.get("list") or [])


def ranking(client: BiliClient, rid: int = 0) -> list[dict]:
    data = client.api("/x/web-interface/ranking/v2", {"rid": rid, "type": "all"})
    return _clean(data.get("list") or [])


def search(client: BiliClient, keyword: str, page: int = 1, page_size: int = 20) -> list[dict]:
    params = {"keyword": keyword, "search_type": "video", "page": page,
              "page_size": page_size}
    try:
        data = client.api("/x/web-interface/wbi/search/type", params, wbi=True)
    except BiliError as e:
        if e.code not in (-400, -403, -412):
            raise
        data = client.api("/x/web-interface/search/type", params)
    result = data.get("result") or []
    return _clean(result)


def mixed(client: BiliClient, ps: int = 30) -> list[dict]:
    """推荐 + 热门去重，给「自主刷视频」用。"""
    out = recommend(client, ps=min(ps, 20))
    seen = {x["bvid"] for x in out}
    try:
        for x in popular(client, ps=min(ps, 20)):
            if x["bvid"] not in seen:
                out.append(x)
                seen.add(x["bvid"])
    except BiliError:
        pass
    return out
