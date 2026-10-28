# -*- coding: utf-8 -*-
"""扫码登录：生成二维码、轮询状态、落盘 cookie（DPAPI）。"""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional

from app.secure import protect_text
from .client import BiliClient, UA
from .errors import BiliError

QR_GENERATE = "https://passport.bilibili.com/x/passport-login/web/qrcode/generate"
QR_POLL = "https://passport.bilibili.com/x/passport-login/web/qrcode/poll"
_HEADERS = {
    "User-Agent": UA,
    "Referer": "https://www.bilibili.com/",
    "Origin": "https://www.bilibili.com",
    "Accept": "application/json, text/plain, */*",
}
_COOKIE_KEYS = ("SESSDATA", "bili_jct", "DedeUserID", "DedeUserID__ckMd5", "buvid3", "buvid4")


def _request_json(url: str, params: Optional[dict] = None, timeout: float = 12.0):
    qs = urllib.parse.urlencode(params or {})
    req = urllib.request.Request(url + (("&" if "?" in url else "?") + qs if qs else ""),
                                 headers=_HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", "replace")
        cookies = resp.headers.get_all("Set-Cookie") or []
    try:
        body = json.loads(raw)
    except ValueError as e:
        raise BiliError(f"登录接口返回非 JSON：{raw[:120]}") from e
    if not isinstance(body, dict):
        raise BiliError("登录接口返回格式异常")
    return body, cookies


def _parse_set_cookie(headers: list[str]) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for h in headers:
        first = str(h or "").split(";", 1)[0]
        if "=" in first:
            k, v = first.split("=", 1)
            if k.strip() and v.strip():
                pairs[k.strip()] = v.strip()
    return pairs


def generate() -> dict:
    body, _ = _request_json(QR_GENERATE, {"source": "main-fe-header"})
    if int(body.get("code") or 0) != 0:
        raise BiliError(f"生成二维码失败：{body.get('message') or body.get('code')}")
    data = body.get("data") or {}
    url = str(data.get("url") or "")
    key = str(data.get("qrcode_key") or "")
    if not url or not key:
        raise BiliError("生成二维码返回缺 url/qrcode_key")
    return {"url": url, "key": key, "expires_in": 180}


def poll(key: str) -> dict:
    body, set_cookies = _request_json(QR_POLL, {"qrcode_key": key, "source": "main-fe-header"})
    data = body.get("data") or {}
    code = int(data.get("code") if data.get("code") is not None else body.get("code") or 0)
    if code == 86101:
        return {"status": "waiting", "code": code, "message": "等待扫码"}
    if code == 86090:
        return {"status": "scanned", "code": code, "message": "已扫码，请在手机上确认"}
    if code == 86038:
        return {"status": "expired", "code": code, "message": "二维码已过期，请重新生成"}
    if code != 0:
        return {"status": "error", "code": code,
                "message": str(data.get("message") or body.get("message") or f"登录失败 code={code}")}
    pairs = _parse_set_cookie(set_cookies)
    if not pairs.get("SESSDATA"):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(str(data.get("url") or "")).query)
        for k in _COOKIE_KEYS:
            if q.get(k) and not pairs.get(k):
                pairs[k] = q[k][0]
    if not pairs.get("SESSDATA"):
        return {"status": "error", "code": 0, "message": "登录成功但没拿到 SESSDATA，请重试"}
    return {"status": "ok", "code": 0, "message": "登录成功", "cookies": pairs, "data": data}


def save_cookie(client: BiliClient, path: Path) -> dict:
    pairs = {k: str(v) for k, v in client.cookie_pairs.items() if v}
    payload = {k: pairs.get(k, "") for k in _COOKIE_KEYS if pairs.get(k)}
    payload["updated"] = time.time()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False)
    protected = protect_text(text)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(protected, encoding="utf-8")
    tmp.replace(path)
    return {"path": str(path), "keys": sorted(payload.keys()),
            "protected": protected.startswith("dpapi:")}


def finish_login(client: BiliClient, cookies: dict, path: Path) -> dict:
    client.add_cookie_pairs({k: v for k, v in cookies.items() if v})
    try:
        client.ensure_buvid()
    except Exception:
        pass
    info = client.nav()
    if not info.get("isLogin"):
        raise BiliError("扫码成功但 nav 仍显示未登录，cookie 可能已失效")
    saved = save_cookie(client, path)
    return {"user": info, "cookie": saved}


def status(client: BiliClient) -> dict:
    try:
        return client.nav()
    except BiliError as e:
        return {"isLogin": False, "uname": "", "mid": 0, "error": str(e)}
