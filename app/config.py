# -*- coding: utf-8 -*-
"""配置：config.json 深合并默认值，运行时热更新。"""
from __future__ import annotations

import copy
import json
import threading
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parent.parent

DEFAULTS: dict[str, Any] = {
    "app": {
        "host": "127.0.0.1",
        "port": 8765,
        "open_browser": True,
        "log_level": "INFO",
    },
    "bili": {
        "cookie_file": "",
        "allow_write": True,
        "timeout_s": 15,
        "user_agent": "",
        "ffmpeg": "",
    },
    "llm": {
        "base_url": "http://127.0.0.1:8081/v1",
        "api_key": "sk-local",
        "model": "qwen3.5-4b",
        "timeout_s": 180,
        "temperature": 0.9,
        "max_tokens": 320,
        "json_mode": False,
        "disable_thinking": True,
        "adapter": "",
    },
    "bot": {
        "enabled": False,
        "auto_start": False,
        "approval_mode": "auto",
        "active_hours": ["09:00-12:00", "14:00-23:30"],
        "comment_per_day": 8,
        "nested_per_day": 15,
        "dm_per_day": 5,
        "min_interval_min": 20,
        "max_interval_min": 60,
        "cycle_min_s": 120,
        "cycle_max_s": 420,
        "browse_per_comment": [3, 8],
        "comment_probability": 0.55,
        "dwell_ratio": [0.3, 0.8],
        "dwell_cap_s": 480,
        "read_comments_s": [10, 60],
        "max_nested_per_thread": 3,
        "search_interests": [],
        "use_tags": True,
        "use_subtitle": True,
        "use_danmaku": True,
        "use_recommend": True,
        "use_search": False,
        "like_video": False,
        "simulate_dwell": True,
        "dry_run_sleep": False,
        "at_master": {
            "mode": "taste",
            "uid": 0,
            "name": "",
            "daily_limit": 8,
            "taste": [],
            "append_text": True,
        },
        "dm": {
            "reply_incoming": True,
            "proactive": "whitelist",
            "whitelist": [],
            "proactive_daily": 1,
        },
    },
    "risk": {
        "daily_total_actions": 40,
        "cooldown_on_risk_h": 6,
        "max_consecutive_failures": 3,
        "risk_codes": [-352, -412, -509, -799, 86038],
    },
    "persona_file": "personas/default.json",
    "voice": {
        "enabled": True,
        "ffmpeg": "",
        "asr": {
            "enabled": True,
            "model_dir": "~/.dsh/speech-to-text/sensevoice/models",
            "threads": 4,
            "language": "zh",
            "use_itn": True,
            "for_video": True,
            "max_seconds": 120,
            "in_dry_run": False,
        },
    },
    "train": {
        "exclude_ranges": [["2026-10-01", "2026-11-01"]],
        "target_per_video": 50,
    },
}


def deep_merge(base: Any, patch: Any) -> Any:
    if isinstance(base, dict) and isinstance(patch, dict):
        out = copy.deepcopy(base)
        for k, v in patch.items():
            out[k] = deep_merge(out.get(k), v) if k in out else copy.deepcopy(v)
        return out
    return copy.deepcopy(patch)


class Config:
    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path) if path else ROOT / "config.json"
        self._lock = threading.RLock()
        self.raw: dict[str, Any] = copy.deepcopy(DEFAULTS)
        if self.path.exists():
            try:
                loaded = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    self.raw = deep_merge(DEFAULTS, loaded)
            except (OSError, ValueError):
                pass
        else:
            self.save()
        self.ensure_dirs()

    # ------------------------------------------------------------------ 路径
    @property
    def root(self) -> Path:
        return self.path.parent

    @property
    def data_dir(self) -> Path:
        return self.root / "data"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "bot.sqlite3"

    @property
    def cookie_path(self) -> Path:
        explicit = str(self.get("bili.cookie_file") or "").strip()
        if explicit:
            p = Path(explicit).expanduser()
            return p if p.is_absolute() else self.root / p
        return self.data_dir / "bili_cookie.json"

    @property
    def log_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    @property
    def voice_dir(self) -> Path:
        return self.data_dir / "voice"

    @property
    def persona_path(self) -> Path:
        p = Path(str(self.get("persona_file") or "personas/default.json"))
        return p if p.is_absolute() else self.root / p

    def ensure_dirs(self) -> None:
        for p in (self.data_dir, self.log_dir, self.cache_dir, self.voice_dir, self.persona_path.parent):
            p.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ 读写
    def get(self, dotted: str, default: Any = None) -> Any:
        with self._lock:
            cur: Any = self.raw
            for part in dotted.split("."):
                if not isinstance(cur, dict) or part not in cur:
                    return default
                cur = cur[part]
            return cur

    def save(self) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp.write_text(json.dumps(self.raw, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self.path)

    def update(self, patch: dict) -> dict:
        with self._lock:
            self.raw = deep_merge(self.raw, patch or {})
            self.save()
            return copy.deepcopy(self.raw)

    def reload(self) -> dict:
        with self._lock:
            if self.path.exists():
                try:
                    loaded = json.loads(self.path.read_text(encoding="utf-8"))
                    if isinstance(loaded, dict):
                        self.raw = deep_merge(DEFAULTS, loaded)
                except (OSError, ValueError):
                    pass
            self.ensure_dirs()
            return copy.deepcopy(self.raw)

    def snapshot(self) -> dict:
        with self._lock:
            return copy.deepcopy(self.raw)
