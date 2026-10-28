# -*- coding: utf-8 -*-
"""BiliBot 入口：启动本地控制台 + 机器人引擎。

用法：
  py -3 app/main.py                 # 启动控制台，浏览器打开
  py -3 app/main.py --no-open       # 不自动开浏览器
  py -3 app/main.py --once          # 单跑一轮 dry-run（生成草稿，不发布）
  py -3 app/main.py --once --live   # 单跑一轮并真实发布（谨慎）
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import Config  # noqa: E402
from app.log import get, setup  # noqa: E402
from app.robot.engine import Engine  # noqa: E402
from app.store import Store  # noqa: E402
from app.web.server import WebServer  # noqa: E402


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="BiliBot 本地 B 站小号机器人")
    p.add_argument("--config", default="", help="配置文件路径（默认项目根 config.json）")
    p.add_argument("--host", default="", help="控制台绑定地址（默认 127.0.0.1）")
    p.add_argument("--port", type=int, default=0, help="控制台端口（默认 8765，占用则 +1）")
    p.add_argument("--no-open", action="store_true", help="不自动打开浏览器")
    p.add_argument("--once", action="store_true", help="单跑一轮后退出")
    p.add_argument("--live", action="store_true", help="配合 --once：真实发布（默认 dry-run）")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    cfg = Config(Path(args.config) if args.config else None)
    if args.host:
        cfg.update({"app": {"host": args.host}})
    if args.port:
        cfg.update({"app": {"port": int(args.port)}})
    cfg.ensure_dirs()

    setup(cfg.log_dir, str(cfg.get("app.log_level", "INFO") or "INFO"))
    log = get("main")
    log.info("BiliBot 启动，配置：%s", cfg.path)

    store = Store(cfg.db_path)
    engine = Engine(cfg, store, log)

    if args.once:
        res = engine.run_once(dry_run=not args.live)
        print(json.dumps(res, ensure_ascii=False, indent=2, default=str))
        return 0 if res.get("ok") else 1

    server = WebServer(cfg, store, engine, log)
    try:
        url = server.start()
    except Exception as e:  # noqa: BLE001
        log.error("控制台启动失败：%s", e)
        print(f"控制台启动失败：{e}", file=sys.stderr)
        return 1

    print("=" * 56)
    print("  BiliBot 本地 B 站小号机器人")
    print(f"  控制台：{url}")
    print("  第一次使用：登录 → 设置模型 → 单跑 dry-run → 开始")
    print("  停止：Ctrl+C")
    print("=" * 56)
    if not args.no_open and bool(cfg.get("app.open_browser", True)):
        try:
            webbrowser.open(url)
        except Exception:
            pass

    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        print("\n正在退出…")
    finally:
        try:
            engine.stop()
        except Exception:
            pass
        try:
            server.stop()
        except Exception:
            pass
        log.info("BiliBot 已退出")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
