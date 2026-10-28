# -*- coding: utf-8 -*-
r"""整夜“魔鬼化训练”流水线：等采集 → 构造 SFT → 连续训练多个 QLoRA 变体 →
转 GGUF → 逐个起 llama-server 评测 → 选最佳 → 写报告并切默认 adapter。

用法：
  runtime\python312\python.exe train\night_run.py --target-videos 170 --deadline 05:10
日志：data/train/night_run.log（由启动者重定向）
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY312 = ROOT / "runtime" / "python312" / "python.exe"
LLAMA = ROOT / "runtime" / "llama" / "llama-server.exe"
BASE_GGUF = ROOT / "models" / "Qwen3.5-4B-Q4_K_M.gguf"
HF_DIR = ROOT / "models" / "Qwen3.5-4B-hf"
CONVERT = ROOT / "tools" / "llama.cpp" / "llama.cpp-master" / "convert_lora_to_gguf.py"
RAW = ROOT / "data" / "train" / "raw_mega.jsonl"
TRAIN_DIR = ROOT / "data" / "train"
REPORT_JSON = TRAIN_DIR / "night_report.json"
REPORT_MD = TRAIN_DIR / "night_report.md"


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def count_lines(path: Path) -> int:
    if not path.exists():
        return 0
    n = 0
    with path.open("rb") as f:
        for _ in f:
            n += 1
    return n


def run(cmd: list[str], log_path: Path, timeout: int = 3600) -> tuple[int, str]:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8", errors="replace") as f:
        f.write("$ " + " ".join(str(x) for x in cmd) + "\n\n")
        f.flush()
        try:
            p = subprocess.run(cmd, cwd=str(ROOT), stdout=f, stderr=subprocess.STDOUT,
                               timeout=timeout, text=True, encoding="utf-8", errors="replace")
            return p.returncode, ""
        except subprocess.TimeoutExpired:
            return -9, f"timeout {timeout}s"
        except Exception as e:  # noqa: BLE001
            return -1, repr(e)


def port_pids(port: int) -> list[int]:
    pids: set[int] = set()
    try:
        raw = subprocess.run(["netstat", "-ano", "-p", "tcp"], capture_output=True,
                             timeout=15).stdout or b""
        out = raw.decode("mbcs", errors="replace")
    except Exception:
        return []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[0].upper() == "TCP" and parts[3].upper() == "LISTENING":
            if parts[1].endswith(f":{port}"):
                try:
                    pids.add(int(parts[4]))
                except ValueError:
                    pass
    return sorted(pids)


def kill_port(port: int) -> None:
    for pid in port_pids(port):
        log(f"kill :{port} pid={pid}")
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, text=True)


def wait_health(proc: subprocess.Popen, port: int, seconds: int = 120) -> bool:
    for _ in range(seconds):
        if proc.poll() is not None:
            return False
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/v1/models", timeout=2) as r:
                if r.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(1)
    return False


def start_llama(adapter: Path | None, port: int, log_path: Path) -> subprocess.Popen | None:
    cmd = [str(LLAMA), "--model", str(BASE_GGUF), "--host", "127.0.0.1", "--port", str(port),
           "-c", "4096", "-ngl", "99", "--jinja", "--no-webui"]
    if adapter:
        cmd += ["--lora", str(adapter)]
    log_path.parent.mkdir(parents=True, exist_ok=True)
    f = log_path.open("w", encoding="utf-8", errors="replace")
    f.write("$ " + " ".join(cmd) + "\n\n")
    f.flush()
    p = subprocess.Popen(cmd, cwd=str(ROOT), stdout=f, stderr=subprocess.STDOUT)
    if not wait_health(p, port, 150):
        log(f"llama :{port} 未就绪，退出码 {p.poll()}")
        try:
            p.kill()
        except Exception:
            pass
        f.close()
        return None
    return p


def stop_proc(p: subprocess.Popen) -> None:
    if p.poll() is None:
        subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"], capture_output=True, text=True)
    try:
        p.wait(timeout=10)
    except Exception:
        pass


def build_sft(name: str, min_target: int, min_nested: int) -> dict:
    out = TRAIN_DIR / name
    log(f"build_sft {name} (target>={min_target}, nested>={min_nested})")
    code, err = run([sys.executable, "train/build_sft.py", "--raw", str(RAW),
                     "--out-dir", str(out), "--val-ratio", "0.08",
                     "--min-target-like", str(min_target),
                     "--min-nested-like", str(min_nested)],
                    TRAIN_DIR / f"build_{name}.log", timeout=600)
    stats = {}
    if (out / "stats.json").exists():
        stats = json.loads((out / "stats.json").read_text(encoding="utf-8"))
    log(f"build_sft {name}: code={code} {stats} {err}")
    return stats


def train_variant(v: dict) -> dict:
    name = v["name"]
    out = TRAIN_DIR / f"adapter_{name}"
    if out.exists():
        shutil.rmtree(out, ignore_errors=True)
    log(f"train {name}: {v}")
    cmd = [str(PY312), "train/train_qlora.py",
           "--base", str(HF_DIR), "--sft", str(TRAIN_DIR / v["sft"]),
           "--out", str(out), "--epochs", str(v["epochs"]),
           "--max-len", str(v["max_len"]), "--batch", "1",
           "--grad-accum", str(v["grad_accum"]), "--lr", str(v["lr"]),
           "--rank", str(v["rank"]), "--alpha", str(v["alpha"]),
           "--max-samples", str(v["max_samples"])]
    code, err = run(cmd, TRAIN_DIR / f"train_{name}.log", timeout=v.get("timeout", 4200))
    ok = (out / "adapter_model.safetensors").exists()
    loss = None
    try:
        txt = (TRAIN_DIR / f"train_{name}.log").read_text(encoding="utf-8", errors="replace")
        m = re.findall(r"'train_loss':\s*([0-9.]+)", txt)
        if m:
            loss = float(m[-1])
    except Exception:
        pass
    log(f"train {name}: code={code} ok={ok} loss={loss} {err}")
    return {"name": name, **v, "ok": ok, "code": code, "train_loss": loss, "adapter": str(out)}


def convert_variant(v: dict) -> Path | None:
    name = v["name"]
    adapter = TRAIN_DIR / f"adapter_{name}"
    out = ROOT / "models" / f"qwen3.5-4b-lora-{name}.gguf"
    if not (adapter / "adapter_model.safetensors").exists():
        return None
    log(f"convert {name}")
    code, err = run([str(PY312), str(CONVERT), "--base", str(HF_DIR), "--outfile", str(out), str(adapter)],
                    TRAIN_DIR / f"convert_{name}.log", timeout=900)
    if code != 0 or not out.exists():
        log(f"convert {name} failed code={code} {err}")
        return None
    log(f"convert {name}: {out} {out.stat().st_size} bytes")
    return out


def eval_variant(name: str, adapter: Path | None, limit: int) -> dict:
    kill_port(8082)
    proc = start_llama(adapter, 8082, TRAIN_DIR / f"eval_llama_{name}.log")
    if not proc:
        return {"name": name, "ok": False, "error": "llama not ready"}
    try:
        cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
        cfg["llm"]["base_url"] = "http://127.0.0.1:8082/v1"
        cfg["llm"]["adapter"] = ""
        tmp = TRAIN_DIR / f"eval_config_{name}.json"
        tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        out = TRAIN_DIR / f"eval_{name}.json"
        code, err = run([str(PY312), "train/eval.py", "--config", str(tmp),
                         "--sft", str(TRAIN_DIR / "sft_mega"), "--limit", str(limit),
                         "--out", str(out), "--blind", str(TRAIN_DIR / f"blind_{name}.html")],
                        TRAIN_DIR / f"eval_{name}.log", timeout=1200)
        if not out.exists():
            return {"name": name, "ok": False, "error": f"eval code={code} {err}"}
        data = json.loads(out.read_text(encoding="utf-8"))
        data.pop("samples", None)
        data["name"] = name
        data["adapter"] = str(adapter) if adapter else ""
        data["ok"] = True
        log(f"eval {name}: {json.dumps({k: data.get(k) for k in ('avg_len','distinct_2','exact_dup_rate','ref_overlap','ai_word_rate')}, ensure_ascii=False)}")
        return data
    except Exception as e:  # noqa: BLE001
        return {"name": name, "ok": False, "error": repr(e)}
    finally:
        stop_proc(proc)


def score(metrics: dict) -> float:
    if not metrics.get("ok"):
        return -99
    return (
        3.0 * float(metrics.get("distinct_2") or 0)
        + 5.0 * float(metrics.get("ref_overlap") or 0)
        - 8.0 * float(metrics.get("exact_dup_rate") or 0)
        - 10.0 * float(metrics.get("ai_word_rate") or 0)
        - 4.0 * float(metrics.get("url_rate") or 0)
        - 0.03 * max(0.0, float(metrics.get("avg_len") or 0) - 30.0)
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target-videos", type=int, default=170)
    ap.add_argument("--deadline", default="05:10", help="HH:MM，采集等待截止")
    ap.add_argument("--eval-limit", type=int, default=117)
    args = ap.parse_args()

    TRAIN_DIR.mkdir(parents=True, exist_ok=True)
    log(f"start; raw={RAW} lines={count_lines(RAW)} target={args.target_videos}")

    hh, mm = args.deadline.split(":")
    deadline = datetime.now().replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
    if deadline < datetime.now():
        deadline = datetime.now()
    while count_lines(RAW) < args.target_videos and datetime.now() < deadline:
        log(f"wait collect: {count_lines(RAW)}/{args.target_videos}")
        time.sleep(60)
    total = count_lines(RAW)
    log(f"collect wait done: {total} videos")
    if total < 40:
        log("数据太少，中止")
        return 1

    stats_mega = build_sft("sft_mega", 30, 5)
    stats_strict = build_sft("sft_mega_strict", 50, 10)
    sft_strict = "sft_mega"
    if int(stats_strict.get("train") or 0) >= 600:
        sft_strict = "sft_mega_strict"

    variants = [
        {"name": "mega_a", "sft": "sft_mega", "epochs": 2, "max_len": 384,
         "grad_accum": 8, "lr": 2e-4, "rank": 16, "alpha": 32, "max_samples": 1800},
        {"name": "mega_b", "sft": sft_strict, "epochs": 2, "max_len": 384,
         "grad_accum": 8, "lr": 1e-4, "rank": 32, "alpha": 64, "max_samples": 1500},
        {"name": "mega_c", "sft": "sft_mega", "epochs": 3, "max_len": 384,
         "grad_accum": 8, "lr": 1e-4, "rank": 16, "alpha": 32, "max_samples": 1400},
    ]

    kill_port(8081)
    kill_port(8082)
    time.sleep(3)

    trained: list[dict] = []
    for v in variants:
        try:
            r = train_variant(v)
            if r["ok"]:
                gguf = convert_variant(v)
                r["gguf"] = str(gguf) if gguf else ""
            trained.append(r)
        except Exception as e:  # noqa: BLE001
            trained.append({"name": v["name"], "ok": False, "error": repr(e)})
            log(f"variant {v['name']} crashed: {e!r}")

    # 若 rank32 失败，补一个 rank16 的 B 变体
    if not any(t.get("name") == "mega_b" and t.get("ok") for t in trained):
        fb = {"name": "mega_b2", "sft": sft_strict, "epochs": 2, "max_len": 384,
              "grad_accum": 8, "lr": 1e-4, "rank": 16, "alpha": 32, "max_samples": 1500}
        try:
            r = train_variant(fb)
            if r["ok"]:
                gguf = convert_variant(fb)
                r["gguf"] = str(gguf) if gguf else ""
            trained.append(r)
        except Exception as e:  # noqa: BLE001
            log(f"fallback b crashed: {e!r}")

    evals: list[dict] = []
    evals.append(eval_variant("base", None, args.eval_limit))
    for t in trained:
        if not t.get("ok"):
            continue
        gguf = Path(t.get("gguf") or "")
        if not gguf.exists():
            continue
        e = eval_variant(t["name"], gguf, args.eval_limit)
        evals.append(e)

    for e in evals:
        e["score"] = round(score(e), 4)
    best = max(evals, key=lambda x: x.get("score") or -99)
    log(f"best={best['name']} score={best['score']}")

    # 选中的 adapter 复制成稳定名字，更新 config
    chosen_path = ""
    if best["name"] != "base" and best.get("adapter"):
        src = Path(best["adapter"])
        if src.exists():
            dst = ROOT / "models" / "qwen3.5-4b-lora-devil.gguf"
            shutil.copy2(src, dst)
            chosen_path = "models/qwen3.5-4b-lora-devil.gguf"
            cfg_path = ROOT / "config.json"
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
            cfg["llm"]["adapter"] = chosen_path
            cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
            log(f"config adapter -> {chosen_path}")
    else:
        cfg_path = ROOT / "config.json"
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        cfg["llm"]["adapter"] = ""
        cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        log("base 最优，config adapter 置空")

    report = {
        "finished_at": datetime.now().isoformat(timespec="seconds"),
        "raw_videos": total,
        "stats_mega": stats_mega,
        "stats_strict": stats_strict,
        "variants": trained,
        "evals": evals,
        "best": best,
        "chosen_adapter": chosen_path,
    }
    REPORT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# BiliBot 魔鬼化训练报告",
        "",
        f"- 完成时间：{report['finished_at']}",
        f"- 原始视频：{total}",
        f"- SFT：{json.dumps(stats_mega, ensure_ascii=False)}",
        f"- 严格 SFT：{json.dumps(stats_strict, ensure_ascii=False)}",
        "",
        "## 评测",
        "",
        "| 变体 | avg_len | distinct_2 | exact_dup | ref_overlap | AI率 | score |",
        "|---|---|---|---|---|---|---|",
    ]
    for e in evals:
        lines.append(
            f"| {e.get('name')} | {e.get('avg_len')} | {e.get('distinct_2')} | "
            f"{e.get('exact_dup_rate')} | {e.get('ref_overlap')} | {e.get('ai_word_rate')} | {e.get('score')} |"
        )
    lines += ["", f"**选中：{best['name']}**，adapter：`{chosen_path or 'base（不启用）'}`", ""]
    for t in trained:
        lines.append(f"- {t.get('name')}: ok={t.get('ok')} loss={t.get('train_loss')} {t.get('error','')}")
    REPORT_MD.write_text("\n".join(lines), encoding="utf-8")
    log(f"report -> {REPORT_JSON} / {REPORT_MD}")

    # 用最佳 adapter 重启 8081，方便早上打开控制台
    adapter_for_server = ROOT / chosen_path if chosen_path else None
    proc = start_llama(adapter_for_server, 8081, TRAIN_DIR / "llama_morning.log")
    log(f"8081 started: {bool(proc)} adapter={chosen_path or 'base'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
