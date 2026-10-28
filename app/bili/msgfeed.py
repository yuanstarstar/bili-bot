# -*- coding: utf-8 -*-
"""消息中心：回复我的 / @我的。用于发现需要接的楼中楼和 @。"""
from __future__ import annotations

import re
from typing import Optional

from .client import BiliClient
from .errors import BiliError

_AID_RE = re.compile(r"(?:video/)?(?:av)?(\d{3,})", re.I)


def _norm(it: dict) -> Optional[dict]:
    if not isinstance(it, dict):
        return None
    rep = it.get("reply") or {}
    user = it.get("user") or {}
    item = it.get("item") or {}
    content = rep.get("content") or {}
    uri = str(item.get("uri") or "")
    m = _AID_RE.search(uri)
    return {
        "id": it.get("id"),
        "rpid": int(rep.get("rpid") or 0),
        "root": int(rep.get("root") or 0),
        "parent": int(rep.get("parent") or 0),
        "oid": int(rep.get("oid") or 0),
        "aid": int(m.group(1)) if m else int(rep.get("oid") or 0),
        "message": str(content.get("message") or ""),
        "mid": int(user.get("mid") or 0),
        "uname": str(user.get("nickname") or user.get("uname") or ""),
        "uri": uri,
        "title": str((item.get("source_content") or {}).get("title") or ""),
        "type": str(item.get("type") or ""),
        "raw": it,
    }


def replies(client: BiliClient) -> list[dict]:
    data = client.api("/x/msgfeed/reply", {"platform": "web", "build": 0, "mobi_app": "web"})
    out = []
    for it in (data.get("items") or []):
        n = _norm(it)
        if n:
            out.append(n)
    return out


def ats(client: BiliClient) -> list[dict]:
    try:
        data = client.api("/x/msgfeed/at", {"platform": "web", "build": 0, "mobi_app": "web"})
    except BiliError:
        return []
    out = []
    for it in (data.get("items") or []):
        n = _norm(it)
        if n:
            out.append(n)
    return out
