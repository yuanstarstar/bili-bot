# -*- coding: utf-8 -*-
"""熔断：遇到风控码/连续失败就停一段时间，避免越试越封。"""
from __future__ import annotations

import time
from typing import Any, Optional


class Circuit:
    def __init__(self, cfg, store, logger) -> None:
        self.cfg = cfg
        self.store = store
        self.log = logger

    @property
    def codes(self) -> set[int]:
        out = set()
        for x in (self.cfg.get("risk.risk_codes") or []):
            try:
                out.add(int(x))
            except (TypeError, ValueError):
                pass
        return out

    def is_open(self) -> tuple[bool, str]:
        until = float(self.store.get_setting("circuit_until", 0) or 0)
        if until > time.time():
            reason = str(self.store.get_setting("circuit_reason", "") or "")
            left = int((until - time.time()) / 60) + 1
            return True, f"熔断中（{left} 分钟）：{reason}"
        return False, ""

    def trip(self, reason: str, hours: Optional[float] = None) -> None:
        h = float(self.cfg.get("risk.cooldown_on_risk_h", 6) or 6) if hours is None else float(hours)
        until = time.time() + h * 3600
        self.store.set_setting("circuit_until", until)
        self.store.set_setting("circuit_reason", str(reason)[:300])
        self.store.log_action("circuit", "trip", str(reason)[:300], ok=False)
        self.log.warning("熔断 %.1f 小时：%s", h, reason)

    def reset(self) -> None:
        self.store.set_setting("circuit_until", 0)
        self.store.set_setting("circuit_reason", "")
        self.store.set_setting("consecutive_failures", 0)

    def on_success(self) -> None:
        self.store.set_setting("consecutive_failures", 0)

    def on_failure(self, reason: str) -> None:
        n = int(self.store.get_setting("consecutive_failures", 0) or 0) + 1
        self.store.set_setting("consecutive_failures", n)
        max_n = int(self.cfg.get("risk.max_consecutive_failures", 3) or 3)
        if max_n > 0 and n >= max_n:
            self.trip(f"连续失败 {n} 次：{reason}")

    def handle(self, err: Exception) -> bool:
        """识别风控码；命中就熔断并返回 True。"""
        code: Any = getattr(err, "code", None)
        try:
            code_int = int(code) if code is not None else None
        except (TypeError, ValueError):
            code_int = None
        if code_int is not None and code_int in self.codes:
            self.trip(f"风控码 {code_int}：{err}")
            return True
        # 12006/12002：评论或视频已不存在，不是风控，也不该累计熔断。
        if code_int in (12006, 12002):
            return False
        self.on_failure(str(err))
        return False

    def info(self) -> dict:
        until = float(self.store.get_setting("circuit_until", 0) or 0)
        return {
            "open": until > time.time(),
            "until": until,
            "reason": str(self.store.get_setting("circuit_reason", "") or ""),
            "failures": int(self.store.get_setting("consecutive_failures", 0) or 0),
            "codes": sorted(self.codes),
        }
