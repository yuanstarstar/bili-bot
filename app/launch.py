"""一键启动：先拉起内置 llama-server，再启动 Web 控制台。

开发时也可以直接用 `python app/main.py`，本文件只负责「解压即用」的进程编排。
"""
from __future__ import annotations

import os
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERVER = ROOT / "runtime" / "llama" / "llama-server.exe"
MODEL = ROOT / "models" / "Qwen3.5-4B-Q4_K_M.gguf"
HOST = "127.0.0.1"
PORT = 8081


def _up() -> bool:
    try:
        with urllib.request.urlopen(f"http://{HOST}:{PORT}/v1/models", timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def start_llm() -> subprocess.Popen | None:
    if _up():
        print(f"[launch] LLM 已在 {HOST}:{PORT} 运行，直接使用", flush=True)
        return None
    if not SERVER.is_file() or not MODEL.is_file():
        print(f"[launch] 找不到 {SERVER} 或 {MODEL}，跳过内置模型", flush=True)
        return None
    env = os.environ.copy()
    env["PATH"] = str(SERVER.parent) + os.pathsep + str(ROOT / "runtime" / "bin") + os.pathsep + env.get("PATH", "")
    log_dir = ROOT / "data"
    log_dir.mkdir(parents=True, exist_ok=True)
    log = open(log_dir / "llama.log", "ab", buffering=0)
    cmd = [str(SERVER), "--model", str(MODEL), "--host", HOST, "--port", str(PORT),
           "-c", "4096", "-ngl", "99", "--jinja", "--no-webui"]
    try:
        cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
        adapter = str((cfg.get("llm") or {}).get("adapter") or "").strip()
        if adapter:
            p = Path(adapter)
            if not p.is_absolute():
                p = ROOT / p
            if p.is_file():
                cmd += ["--lora", str(p)]
                print(f"[launch] 加载 LoRA adapter：{p}", flush=True)
            else:
                print(f"[launch] adapter 不存在，忽略：{p}", flush=True)
    except Exception as e:
        print(f"[launch] 读取 config.json 失败（忽略 adapter）：{e}", flush=True)
    print("[launch] 启动内置 Qwen3.5-4B …", flush=True)
    p = subprocess.Popen(cmd, cwd=str(ROOT), env=env, stdout=log, stderr=subprocess.STDOUT)
    for i in range(180):
        if p.poll() is not None:
            print(f"[launch] llama-server 退出 code={p.returncode}，看 data/llama.log", flush=True)
            return None
        if _up():
            print(f"[launch] LLM 就绪（{i + 1}s）", flush=True)
            return p
        time.sleep(1)
    print("[launch] LLM 180s 未就绪，先启动网页（可稍后手动重启）", flush=True)
    return p


def main() -> int:
    llm = start_llm()
    args = sys.argv[1:]
    try:
        return subprocess.call([sys.executable, str(ROOT / "app" / "main.py"), *args],
                               cwd=str(ROOT))
    except KeyboardInterrupt:
        return 0
    finally:
        if llm is not None and llm.poll() is None:
            try:
                llm.terminate()
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
