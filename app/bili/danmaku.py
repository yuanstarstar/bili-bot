# -*- coding: utf-8 -*-
"""弹幕：拉 comment.bilibili.com 的 XML，取前 N 条做「现场感」上下文。"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET

from .client import BiliClient

_WS = re.compile(r"\s+")


def _parse_xml(text: str) -> list[tuple[float, str]]:
    out: list[tuple[float, str]] = []
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return out
    for d in root.findall(".//d"):
        msg = _WS.sub(" ", (d.text or "")).strip()
        if not msg:
            continue
        p = (d.get("p") or "").split(",")
        try:
            ts = float(p[0]) if p and p[0] else 0.0
        except ValueError:
            ts = 0.0
        out.append((ts, msg))
    return out


def top(client: BiliClient, cid: int, limit: int = 40, timeout: float = 12.0) -> list[str]:
    if cid <= 0:
        return []
    url = f"https://comment.bilibili.com/{int(cid)}.xml"
    try:
        status, text = client.request(url, headers={"Referer": "https://www.bilibili.com/"},
                                      timeout=timeout)
    except Exception:
        return []
    if status != 200 or not text:
        return []
    items = _parse_xml(text)
    items.sort(key=lambda x: x[0])
    if len(items) > limit:
        step = len(items) / float(limit)
        items = [items[int(i * step)] for i in range(limit)]
    return [m for _, m in items]
