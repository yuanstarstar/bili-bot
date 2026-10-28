# -*- coding: utf-8 -*-
"""节奏控制：活跃时段、每日配额、最小间隔、停留时长。"""
from __future__ import annotations

import random
import time
from datetime import datetime
from typing import Any

CAP_KEY = {
    "comment": "bot.comment_per_day",
    "nested": "bot.nested_per_day",
    "dm": "bot.dm_per_day",
}


class RateLimiter:
    def __init__(self, cfg, store) -> None:
        self.cfg = cfg
        self.store = store

    def today_start(self) -> float:
        return self.store.today_start()

    def active_now(self) -> tuple[bool, str]:
        windows = self.cfg.get("bot.active_hours") or []
        lt = time.localtime()
        cur = lt.tm_hour * 60 + lt.tm_min
        for w in windows:
            try:
                a, b = str(w).split("-", 1)
                ah, am = [int(x) for x in a.strip().split(":")]
                bh, bm = [int(x) for x in b.strip().split(":")]
                start, end = ah * 60 + am, bh * 60 + bm
            except (ValueError, TypeError):
                continue
            if start <= end:
                if start <= cur < end:
                    return True, f"活跃时段 {w}"
            elif cur >= start or cur < end:
                return True, f"活跃时段 {w}（跨夜）"
        return False, f"{lt.tm_hour:02d}:{lt.tm_min:02d} 不在活跃时段"

    def counts_today(self) -> dict[str, int]:
        start = self.today_start()
        return {k: self.store.count_actions(k, start) for k in ("comment", "nested", "dm")}

    def can_write(self, kind: str, ignore_hours: bool = False,
                  ignore_gap: bool = False, ignore_cap: bool = False) -> tuple[bool, str]:
        if not ignore_hours:
            ok, why = self.active_now()
            if not ok:
                return False, why
        if kind not in CAP_KEY:
            return False, f"未知动作 {kind}"
        cap = int(self.cfg.get(CAP_KEY[kind], 0) or 0)
        used = self.store.count_actions(kind, self.today_start())
        if cap <= 0 and not ignore_cap:
            return False, f"{kind} 已关闭（每日配额=0）"
        if cap > 0 and used >= cap:
            return False, f"{kind} 今日已用 {used}/{cap}"
        total_cap = int(self.cfg.get("risk.daily_total_actions", 0) or 0)
        if total_cap > 0:
            total = self.store.count_kinds(("comment", "nested", "dm"), self.today_start())
            if total >= total_cap:
                return False, f"今日总动作 {total}/{total_cap}"
        gap_min = float(self.cfg.get("bot.min_interval_min", 0) or 0)
        if gap_min > 0 and not ignore_gap:
            # 私信回复不应重置“评论间隔”；否则回一条私信会把下一轮评论推迟 20 分钟。
            gap_kinds = ("comment", "nested") if kind in ("comment", "nested") else ("dm",)
            last = self.store.last_action_ts(gap_kinds)
            if last and time.time() - last < gap_min * 60:
                wait = int((gap_min * 60 - (time.time() - last)) / 60) + 1
                return False, f"距上次写操作不足 {gap_min:.0f} 分钟（还要约 {wait} 分钟）"
        return True, "ok"

    def interval_seconds(self) -> float:
        lo = float(self.cfg.get("bot.min_interval_min", 20) or 20)
        hi = float(self.cfg.get("bot.max_interval_min", 60) or 60)
        if hi < lo:
            lo, hi = hi, lo
        return random.uniform(lo * 60, hi * 60)

    def cycle_seconds(self) -> float:
        lo = float(self.cfg.get("bot.cycle_min_s", 120) or 120)
        hi = float(self.cfg.get("bot.cycle_max_s", 420) or 420)
        if hi < lo:
            lo, hi = hi, lo
        return random.uniform(lo, hi)

    def dwell_seconds(self, duration: int) -> float:
        ratio = self.cfg.get("bot.dwell_ratio") or [0.3, 0.8]
        try:
            lo, hi = float(ratio[0]), float(ratio[1])
        except (TypeError, ValueError, IndexError):
            lo, hi = 0.3, 0.8
        cap = float(self.cfg.get("bot.dwell_cap_s", 480) or 480)
        base = max(0, int(duration or 0)) * random.uniform(lo, hi)
        return max(20.0, min(cap, base))

    def read_seconds(self) -> float:
        rng = self.cfg.get("bot.read_comments_s") or [10, 60]
        try:
            return random.uniform(float(rng[0]), float(rng[1]))
        except (TypeError, ValueError, IndexError):
            return 30.0

    def at_master_allowed(self) -> bool:
        limit = int(self.cfg.get("bot.at_master.daily_limit", 8) or 0)
        if limit == 0:
            return True
        row = self.store.query_one(
            "SELECT COUNT(*) AS n FROM our_comments WHERE at_uid>0 AND status!='shadow' AND posted_at>=?",
            (self.today_start(),),
        )
        return int((row or {}).get("n") or 0) < limit

    def remaining(self, kind: str) -> int:
        cap = int(self.cfg.get(CAP_KEY.get(kind, ""), 0) or 0)
        if cap <= 0:
            return 0
        return max(0, cap - self.store.count_actions(kind, self.today_start()))
