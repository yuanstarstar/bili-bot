# -*- coding: utf-8 -*-
"""对当前模型做一轮评论风格评测。

用法：
  py -3 train/eval.py --sft data/train/sft --limit 30 --out data/train/eval.json
  py -3 train/eval.py --sft data/train/sft --limit 30 --blind data/train/blind.html

指标（不依赖第三方库）：
  - AI 腔词命中率
  - 平均长度 / 标点率 / emoji 率
  - 字符 bigram 去重率（distinct-2）
  - 与参考评论的字符 bigram 重合（self-BLEU 的粗略替代）
  - 完全重复率
  - 可选：生成盲测 HTML，人工标记哪条更像真人
"""
from __future__ import annotations

import argparse
import html
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.brain.llm import LLM  # noqa: E402
from app.brain.persona import load as load_persona, system as persona_system  # noqa: E402
from app.config import Config  # noqa: E402

AI_WORDS = ("首先", "其次", "总之", "不得不说", "综上所述", "希望对你有帮助",
            "总的来说", "值得一", "作为 AI", "作为一个", "语言模型")
URL = re.compile(r"(https?://|www\.|b23\.tv)")
CN = re.compile(r"[\u4e00-\u9fff]")
PUNCT = re.compile(r"[，。！？、～…,.!?~]")


def clean(s: str) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip()


def bigrams(s: str) -> list[str]:
    s = clean(s)
    return [s[i:i + 2] for i in range(max(0, len(s) - 1))]


def distinct_n(texts: list[str], n: int = 2) -> float:
    grams = []
    for t in texts:
        grams.extend(bigrams(t) if n == 2 else [t[i:i + n] for i in range(max(0, len(t) - n + 1))])
    return len(set(grams)) / max(1, len(grams))


def overlap(a: str, b: str) -> float:
    ga, gb = Counter(bigrams(a)), Counter(bigrams(b))
    if not ga or not gb:
        return 0.0
    inter = sum((ga & gb).values())
    return inter / max(1, sum(ga.values()))


def load_samples(sft: Path, limit: int) -> list[dict]:
    path = sft / "val.jsonl"
    if not path.exists() or path.stat().st_size == 0:
        path = sft / "train.jsonl"
    out = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            conv = obj.get("conversations") or []
            if len(conv) < 2:
                continue
            human = next((c.get("value") for c in conv if c.get("from") == "human"), "")
            gold = next((c.get("value") for c in conv if c.get("from") == "gpt"), "")
            if human and gold:
                out.append({"prompt": human, "gold": gold, "meta": obj.get("meta", {})})
            if len(out) >= limit:
                break
    return out


def evaluate(cfg: Config, samples: list[dict]) -> dict:
    llm = LLM(cfg)
    persona = load_persona(cfg.persona_path)
    system = persona_system(persona, "comment")
    rows = []
    for i, s in enumerate(samples, 1):
        try:
            out = llm.chat([
                {"role": "system", "content": system},
                {"role": "user", "content": s["prompt"]},
            ], temperature=0.9, max_tokens=80)
        except Exception as e:
            out = f"__ERROR__ {e}"
        out = clean(out).strip("\"'“”")
        rows.append({**s, "gen": out})
        print(f"[{i}/{len(samples)}] {out[:50]}", flush=True)
    gens = [r["gen"] for r in rows if not r["gen"].startswith("__ERROR__")]
    golds = [r["gold"] for r in rows]
    lengths = [len(g) for g in gens]
    ai_hits = sum(1 for g in gens if any(w in g for w in AI_WORDS))
    punct = sum(1 for g in gens if PUNCT.search(g))
    emoji = sum(1 for g in gens if re.search(r"[\U0001F300-\U0001FAFF\u2600-\u27BF]", g))
    url_bad = sum(1 for g in gens if URL.search(g))
    exact_dup = 1 - len(set(gens)) / max(1, len(gens))
    ref_overlap = sum(overlap(g, r["gold"]) for g, r in zip(gens, rows)) / max(1, len(gens))
    return {
        "count": len(rows),
        "errors": len(rows) - len(gens),
        "avg_len": round(sum(lengths) / max(1, len(lengths)), 2),
        "min_len": min(lengths) if lengths else 0,
        "max_len": max(lengths) if lengths else 0,
        "ai_word_rate": round(ai_hits / max(1, len(gens)), 4),
        "punct_rate": round(punct / max(1, len(gens)), 4),
        "emoji_rate": round(emoji / max(1, len(gens)), 4),
        "url_rate": round(url_bad / max(1, len(gens)), 4),
        "distinct_2": round(distinct_n(gens, 2), 4),
        "gold_distinct_2": round(distinct_n(golds, 2), 4),
        "exact_dup_rate": round(exact_dup, 4),
        "ref_overlap": round(ref_overlap, 4),
        "samples": rows,
    }


def write_blind(rows: list[dict], path: Path):
    items = []
    for i, r in enumerate(rows[:40]):
        items.append(f"""
<div class="item">
  <div class="meta">#{i+1} · {html.escape(str(r.get('meta', {}).get('task', '')))}</div>
  <div class="a">{html.escape(r['gen'])}</div>
  <div class="b">{html.escape(r['gold'])}</div>
  <div class="pick">A 更像真人 <input type="radio" name="p{i}" value="A"> &nbsp; B 更像真人 <input type="radio" name="p{i}" value="B"> &nbsp; 差不多 <input type="radio" name="p{i}" value="same"></div>
</div>""")
    path.write_text("""<!doctype html><meta charset="utf-8"><title>BiliBot 盲测</title>
<style>body{background:#111;color:#eee;font:15px/1.7 system-ui;max-width:860px;margin:30px auto;padding:0 18px}
.item{border:1px solid #333;border-radius:12px;padding:14px;margin:14px 0}.meta{color:#888;font-size:12px}
.a,.b{padding:10px;margin:8px 0;border-radius:8px;background:#1b1b1b}.a:before{content:"A：";color:#6ee7d8}
.b:before{content:"B：";color:#5aa9ff}.pick{color:#aaa;font-size:13px}</style>
<h1>BiliBot 盲测</h1><p>随机顺序：A 是模型生成，B 是真实高赞评论。凭直觉选更像真人的一条。</p>
""" + "\n".join(items), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.json")
    ap.add_argument("--sft", default="data/train/sft")
    ap.add_argument("--limit", type=int, default=30)
    ap.add_argument("--out", default="data/train/eval.json")
    ap.add_argument("--blind", default="")
    args = ap.parse_args()
    cfg = Config(args.config)
    samples = load_samples(Path(args.sft), args.limit)
    if not samples:
        raise SystemExit(f"{args.sft} 里没有可用样本，先跑 build_sft.py")
    result = evaluate(cfg, samples)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "samples"}, ensure_ascii=False, indent=2))
    if args.blind:
        write_blind(result["samples"], Path(args.blind))
        print(f"blind test -> {args.blind}")


if __name__ == "__main__":
    main()
