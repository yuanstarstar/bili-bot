# -*- coding: utf-8 -*-
"""字幕：取播放器信息里的字幕列表，拼成纯文本。"""
from __future__ import annotations

from .client import BiliClient
from .errors import BiliError

_PREF = ("zh-CN", "zh-Hans", "zh", "ai-zh", "ai-zh-CN")


def get_subtitles(client: BiliClient, aid: int, cid: int, bvid: str = "",
                  timeout: float = 12.0) -> list[dict]:
    if aid <= 0 or cid <= 0:
        return []
    data = client.api("/x/player/wbi/v2",
                      {"aid": aid, "cid": cid, **({"bvid": bvid} if bvid else {})},
                      wbi=True, timeout=timeout)
    subs = ((data.get("subtitle") or {}).get("subtitles") or [])
    out: list[dict] = []
    for s in subs:
        url = str(s.get("subtitle_url") or "")
        if not url:
            continue
        if url.startswith("//"):
            url = "https:" + url
        try:
            body = client.get_json(url, headers={"Referer": f"https://www.bilibili.com/video/av{aid}"},
                                   timeout=timeout)
            lines = [str(x.get("content") or "").strip() for x in (body.get("body") or [])]
            text = "\n".join(x for x in lines if x)
            if text:
                out.append({"lan": str(s.get("lan") or ""),
                            "lan_doc": str(s.get("lan_doc") or ""), "text": text})
        except (BiliError, ValueError):
            continue
    return out


def best_text(subs: list[dict], max_chars: int = 3500) -> str:
    if not subs:
        return ""
    chosen = None
    for want in _PREF:
        for s in subs:
            if s.get("lan") == want:
                chosen = s
                break
        if chosen:
            break
    chosen = chosen or subs[0]
    text = chosen.get("text") or ""
    if len(text) > max_chars:
        text = text[:max_chars] + "\n…（字幕已截断）"
    return text
