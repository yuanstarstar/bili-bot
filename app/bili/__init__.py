# -*- coding: utf-8 -*-
"""B 站接口层（纯标准库）。

在原 bili_api 基础上补齐：扫码登录、推荐/热门/排行/搜索、字幕、弹幕、
消息中心、私信会话与发送、评论 @ 字段。
"""
from .client import BiliClient, sign_wbi, mixin_key, API_PC, API_LIVE, UA
from .auth import load_cookie, parse_cookie_text, find_cookie_source
from .errors import BiliError, NotLoggedIn, WriteBlocked
from . import live, comments, video, feed, subtitle, danmaku, msgfeed, login, dm

__all__ = [
    "BiliClient", "load_cookie", "parse_cookie_text", "find_cookie_source",
    "BiliError", "NotLoggedIn", "WriteBlocked", "sign_wbi", "mixin_key",
    "API_PC", "API_LIVE", "UA",
    "live", "comments", "video", "feed", "subtitle", "danmaku", "msgfeed",
    "login", "dm",
]
