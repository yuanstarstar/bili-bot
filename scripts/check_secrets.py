# -*- coding: utf-8 -*-
"""开源发布前自检：扫描不该进 Git 的凭据/个人数据。

用法:
    py -3 scripts\\check_secrets.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = {"data", "dist", "models", "runtime", "tools", ".git", "__pycache__",
             "torchinductor_38357", ".venv", "venv"}
TEXT_EXT = {".py", ".json", ".md", ".txt", ".bat", ".ps1", ".js", ".html", ".css",
            ".yml", ".yaml", ".toml", ".ini", ".cfg", ".example", ""}
PATTERNS = [
    ("SESSDATA", re.compile(r"SESSDATA\s*[=:]\s*[A-Za-z0-9%_\-]{16,}", re.I)),
    ("bili_jct", re.compile(r"bili_jct\s*[=:]\s*[A-Fa-f0-9]{16,}", re.I)),
    ("DedeUserID", re.compile(r"DedeUserID\s*[=:]\s*\d{6,}", re.I)),
    ("Cookie JSON", re.compile(r'"(?:SESSDATA|bili_jct|DedeUserID)"\s*:\s*"(?!\s*")[^"]{8,}"')),
    ("api_key 非空", re.compile(r'"api_key"\s*:\s*"(?!sk-local)[^"]{8,}"')),
    ("私钥", re.compile(r"-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----")),
]


def iter_text_files():
    for p in ROOT.rglob("*"):
        if not p.is_file() or p.suffix.lower() not in TEXT_EXT:
            continue
        rel = p.relative_to(ROOT)
        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        if p.name.startswith("_"):
            continue
        if p.name in {"SECURITY.md", "check_secrets.py", "config.json"}:
            continue
        yield p, rel


def main() -> int:
    hits: list[str] = []
    for p, rel in iter_text_files():
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for name, rx in PATTERNS:
            for m in rx.finditer(text):
                line = text.count("\n", 0, m.start()) + 1
                hits.append(f"{rel}:{line}: {name}")
    if hits:
        print("发现疑似敏感内容：")
        for h in hits:
            print(" -", h)
        print("\n请删除/替换后再提交；如果已提交，需要清理 Git 历史。")
        return 1
    print("OK：未发现 Cookie/真实 UID/API key 等敏感内容。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
