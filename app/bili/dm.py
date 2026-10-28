# -*- coding: utf-8 -*-
"""私信：会话列表 / 聊天记录 / 发送。

发送接口来自 dafeiyu-hub 的 bridge（原代码标注未实测），这里做成可回退的
标准库实现；字段名保持 B 站 web 端形状。
"""
from __future__ import annotations

import json
import time
import uuid
from typing import Optional

from .client import BiliClient
from .errors import BiliError

VC = "https://api.vc.bilibili.com"
_HEADERS = {
    "Referer": "https://message.bilibili.com/",
    "Origin": "https://message.bilibili.com",
}


def content_text(msg: dict) -> str:
    raw = msg.get("content")
    obj = raw
    if isinstance(raw, str):
        try:
            obj = json.loads(raw)
        except ValueError:
            return raw
    if isinstance(obj, dict):
        return str(obj.get("content") or obj.get("title") or "")
    return str(raw or "")


def sessions(client: BiliClient) -> list[dict]:
    data = client.api(VC + "/session_svr/v1/session_svr/get_sessions",
                      {"session_type": 1, "group_id": 0, "build": 0, "mobi_app": "web"},
                      headers=_HEADERS)
    out = []
    for s in (data.get("session_list") or []):
        if not isinstance(s, dict):
            continue
        talker = int(s.get("talker_id") or 0)
        if not talker:
            continue
        last = s.get("last_msg") or {}
        out.append({
            "talker_id": talker,
            "uname": str(s.get("name") or s.get("uname") or ""),
            "unread": int(s.get("unread_count") or 0),
            "last_text": content_text(last),
            "last_ts": int(s.get("timestamp") or last.get("timestamp") or 0),
            "raw": s,
        })
    return out


def messages(client: BiliClient, talker_id: int, size: int = 20) -> list[dict]:
    data = client.api(VC + "/svr_sync/v1/svr_sync/fetch_session_msgs",
                      {"talker_id": int(talker_id), "session_type": 1, "size": size,
                       "build": 0, "mobi_app": "web"}, headers=_HEADERS)
    out = []
    for m in (data.get("messages") or []):
        if not isinstance(m, dict):
            continue
        out.append({
            "sender_uid": int(m.get("sender_uid") or 0),
            "receiver_id": int(m.get("receiver_id") or 0),
            "msg_type": int(m.get("msg_type") or 1),
            "content": content_text(m),
            "timestamp": int(m.get("timestamp") or 0),
            "msg_key": str(m.get("msg_key") or ""),
            "raw": m,
        })
    out.sort(key=lambda x: x["timestamp"])
    return out


def send(client: BiliClient, receiver_id: int, text: str) -> dict:
    client.require_login()
    sender = int(client.cookie_pairs.get("DedeUserID") or 0)
    if not sender:
        raise BiliError("cookie 缺 DedeUserID，无法发私信")
    text = str(text or "").strip()
    if not text:
        raise BiliError("私信内容为空")
    if len(text) > 500:
        raise BiliError("私信过长（超过 500 字）")
    data = {
        "msg[sender_uid]": str(sender),
        "msg[receiver_id]": str(int(receiver_id)),
        "msg[receiver_type]": "1",
        "msg[msg_type]": "1",
        "msg[msg_status]": "0",
        "msg[dev_id]": uuid.uuid4().hex,
        "msg[timestamp]": str(int(time.time())),
        "msg[content]": json.dumps({"content": text}, ensure_ascii=False),
        "csrf": client.csrf,
        "csrf_token": client.csrf,
        "build": "0",
        "mobi_app": "web",
    }
    res = client.api_post(VC + "/web_im/v1/web_im/send_msg", data, headers=_HEADERS)
    return {"ok": True, "receiver_id": int(receiver_id), "data": res}
