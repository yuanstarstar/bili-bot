# -*- coding: utf-8 -*-
"""日志：滚动文件 + 控制台 + 内存环形缓冲（给 Web UI 看）。"""
from __future__ import annotations

import logging
import sys
from collections import deque
from logging.handlers import RotatingFileHandler
from pathlib import Path

_RING: deque[str] = deque(maxlen=1200)
_CONFIGURED = False


class RingHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            _RING.append(self.format(record))
        except Exception:
            pass


def setup(log_dir: Path, level: str = "INFO") -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    _CONFIGURED = True
    root = logging.getLogger("bot")
    root.setLevel(getattr(logging, str(level).upper(), logging.INFO))
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s | %(message)s",
                            "%Y-%m-%d %H:%M:%S")
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    root.addHandler(sh)
    try:
        Path(log_dir).mkdir(parents=True, exist_ok=True)
        fh = RotatingFileHandler(Path(log_dir) / "bot.log", maxBytes=5 * 1024 * 1024,
                                 backupCount=3, encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)
    except OSError:
        pass
    rh = RingHandler()
    rh.setFormatter(fmt)
    root.addHandler(rh)


def get(name: str = "bot") -> logging.Logger:
    if name in ("", "bot"):
        return logging.getLogger("bot")
    return logging.getLogger(f"bot.{name}")


def recent(n: int = 200) -> list[str]:
    return list(_RING)[-max(1, int(n)):]
