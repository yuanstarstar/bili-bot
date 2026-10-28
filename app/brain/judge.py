# -*- coding: utf-8 -*-
"""评论质检：清洗、长度、禁词、AI 味、simhash 去重。"""
from __future__ import annotations

import hashlib
import re
from typing import Optional

_URL_RE = re.compile(r"(https?://|www\.)", re.I)
_AI_RE = re.compile(r"(作为(一个)?(ai|人工智能)|我是(ai|机器人|语言模型)|语言模型|提示词|训练数据)", re.I)
_HARD_RE = re.compile(r"(想死吗|想死是吧|去死|杀了你|弄死你|操你|傻逼|贱人|婊子)", re.I)
_LEAD_RE = re.compile(r"^(评论|回复|私信|首评|楼中楼)\s*[:：]\s*")
_NUM_RE = re.compile(r"^\s*(?:\d+[.、)]|[-*•])\s*")
_QUOTES = "\"'“”‘’「」『』"


def clean(text: str) -> str:
    s = str(text or "").strip()
    s = s.replace("\u200b", "").replace("\ufeff", "")
    s = s.replace("\r", " ").replace("\n", " ")
    s = _LEAD_RE.sub("", s)
    s = _NUM_RE.sub("", s)
    while len(s) >= 2 and s[0] in _QUOTES and s[-1] in _QUOTES:
        s = s[1:-1].strip()
    s = re.sub(r"\s+", " ", s).strip()
    # 训练数据/模型常见的“回复 @某人 :”前缀，发出去会显得像机器人
    s = re.sub(r"^回复\s*@[^:：]{1,40}\s*[:：]\s*", "", s).strip()
    return s


def length_range(persona: dict, channel: str) -> tuple[int, int]:
    rng = (persona.get("length") or {}).get(channel) or [6, 30]
    try:
        lo, hi = int(rng[0]), int(rng[1])
    except (TypeError, ValueError):
        lo, hi = 6, 30
    # 私信是聊天，不是评论；对方只发“在吗”时回“在”是正常的，不能因为
    # 人设里的评论长度下限把短回复丢掉，否则会表现为“收到私信但不回”。
    if channel == "dm":
        lo = 1
    return lo, hi


def valid(text: str, persona: dict, channel: str = "comment",
          allow_at: bool = False) -> tuple[bool, str]:
    s = clean(text)
    if not s:
        return False, "空内容"
    lo, hi = length_range(persona, channel)
    if len(s) < lo:
        return False, f"太短（{len(s)}<{lo}）"
    if len(s) > hi:
        return False, f"太长（{len(s)}>{hi}）"
    banned = [str(x) for x in (persona.get("banned_words") or []) if str(x).strip()]
    for w in banned:
        if w in s:
            return False, f"含 AI 腔词：{w}"
    if _URL_RE.search(s):
        return False, "含链接"
    if _AI_RE.search(s):
        return False, "暴露 AI 身份"
    if _HARD_RE.search(s):
        return False, "含威胁/辱骂"
    if "@" in s and not allow_at:
        return False, "未允许 @"
    if s.count("，") + s.count("。") > 6:
        return False, "标点过多，像列点"
    if len(set(s)) <= 2 and len(s) > 3:
        return False, "重复字符"
    return True, "ok"


def simhash(text: str) -> int:
    chars = [c for c in clean(text) if not c.isspace()]
    tokens = chars + [chars[i] + chars[i + 1] for i in range(max(0, len(chars) - 1))]
    if not tokens:
        return 0
    vec = [0] * 64
    for tok in tokens:
        h = int(hashlib.md5(tok.encode("utf-8")).hexdigest()[:16], 16)
        for i in range(64):
            vec[i] += 1 if (h >> i) & 1 else -1
    out = 0
    for i, v in enumerate(vec):
        if v >= 0:
            out |= 1 << i
    return out


def hamming(a: int, b: int) -> int:
    return int(a ^ b).bit_count()


def near_duplicate(text: str, recent: list[str], threshold: int = 4) -> bool:
    h = simhash(text)
    for other in recent:
        if hamming(h, simhash(other)) <= threshold:
            return True
    return False


def pick(candidates: list[str], persona: dict, channel: str = "comment",
         recent: Optional[list[str]] = None, allow_at: bool = False,
         preferred: int = -1) -> tuple[Optional[str], list[dict]]:
    recent = recent or []
    checked: list[dict] = []
    order = list(range(len(candidates)))
    if 0 <= preferred < len(candidates):
        order.remove(preferred)
        order.insert(0, preferred)
    for idx in order:
        raw = candidates[idx]
        s = clean(raw)
        ok, reason = valid(s, persona, channel, allow_at=allow_at)
        if ok and near_duplicate(s, recent):
            ok, reason = False, "和最近发过的太像"
        checked.append({"index": idx, "text": s, "ok": ok, "reason": reason})
        if ok:
            return s, checked
    return None, checked
