# -*- coding: utf-8 -*-
"""Qwen3.5-4B QLoRA 训练脚本（Windows / 8GB 显存保守配置）。

前置（建议在能联网的机器上先装好）：
  runtime\\python312\\python.exe -m pip install torch --index-url https://download.pytorch.org/whl/cu128
  runtime\\python312\\python.exe -m pip install transformers==4.57.0.dev0 peft datasets accelerate bitsandbytes sentencepiece
  # Qwen3.5-4B 是多模态模型，transformers 需要 4.57 dev；若装不到，先换成文本版底座。

用法：
  runtime\\python312\\python.exe train\\train_qlora.py ^
    --base models\\Qwen3.5-4B-hf --sft data\\train\\sft --out data\\train\\adapter ^
    --epochs 2 --max-len 1024 --batch 1 --grad-accum 16

如果 OOM：先把 --max-len 降到 768，或 --grad-accum 加到 24；仍然 OOM 就租 24GB 卡。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def load_rows(path: Path) -> list[dict]:
    rows = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            conv = obj.get("conversations") or []
            human = next((c.get("value") for c in conv if c.get("from") == "human"), "")
            gpt = next((c.get("value") for c in conv if c.get("from") == "gpt"), "")
            if human and gpt:
                rows.append({"prompt": human, "answer": gpt})
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="models/Qwen3.5-4B-hf")
    ap.add_argument("--sft", default="data/train/sft")
    ap.add_argument("--out", default="data/train/adapter")
    ap.add_argument("--epochs", type=float, default=2)
    ap.add_argument("--max-len", type=int, default=1024)
    ap.add_argument("--batch", type=int, default=1)
    ap.add_argument("--grad-accum", type=int, default=16)
    ap.add_argument("--lr", type=float, default=1e-4)
    ap.add_argument("--rank", type=int, default=16)
    ap.add_argument("--alpha", type=int, default=32)
    ap.add_argument("--max-samples", type=int, default=0)
    args = ap.parse_args()

    import torch
    from datasets import Dataset
    from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
    from transformers import (AutoModelForImageTextToText, AutoTokenizer,
                              BitsAndBytesConfig, Trainer, TrainingArguments)

    if not torch.cuda.is_available():
        raise SystemExit("torch.cuda 不可用：先装 CUDA 版 torch，再跑本脚本")

    sft = Path(args.sft)
    train_path = sft / "train.jsonl"
    val_path = sft / "val.jsonl"
    rows = load_rows(train_path)
    if args.max_samples:
        rows = rows[:args.max_samples]
    if not rows:
        raise SystemExit(f"{train_path} 为空，先跑 build_sft.py")
    val_rows = load_rows(val_path) if val_path.exists() and val_path.stat().st_size else rows[:max(1, len(rows)//10)]
    print(f"train={len(rows)} val={len(val_rows)}", flush=True)

    bnb = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_compute_dtype=torch.bfloat16,
        bnb_4bit_use_double_quant=True,
    )
    tokenizer = AutoTokenizer.from_pretrained(args.base, trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    model = AutoModelForImageTextToText.from_pretrained(
        args.base, quantization_config=bnb, device_map={"": 0},
        trust_remote_code=True, torch_dtype=torch.bfloat16,
    )
    model.config.use_cache = False
    model = prepare_model_for_kbit_training(model, use_gradient_checkpointing=True)
    lora = LoraConfig(
        r=args.rank, lora_alpha=args.alpha, lora_dropout=0.05,
        bias="none", task_type="CAUSAL_LM",
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj",
                        "gate_proj", "up_proj", "down_proj"],
    )
    model = get_peft_model(model, lora)
    model.print_trainable_parameters()

    def encode(row: dict) -> dict:
        msgs = [
            {"role": "user", "content": row["prompt"]},
            {"role": "assistant", "content": row["answer"]},
        ]
        full = tokenizer.apply_chat_template(msgs, tokenize=False, add_generation_prompt=False)
        prompt = tokenizer.apply_chat_template(msgs[:1], tokenize=False, add_generation_prompt=True)
        enc = tokenizer(text=full, max_length=args.max_len, truncation=True,
                        return_tensors="pt")
        p = tokenizer(text=prompt, max_length=args.max_len, truncation=True,
                      return_tensors="pt")
        input_ids = enc["input_ids"][0]
        labels = input_ids.clone()
        n = min(len(p["input_ids"][0]), len(labels))
        labels[:n] = -100
        return {
            "input_ids": input_ids,
            "attention_mask": enc["attention_mask"][0],
            "labels": labels,
        }

    train_ds = Dataset.from_list([encode(r) for r in rows])
    val_ds = Dataset.from_list([encode(r) for r in val_rows])

    def collate(batch):
        maxlen = max(len(x["input_ids"]) for x in batch)
        pad = tokenizer.pad_token_id or 0
        out = {"input_ids": [], "attention_mask": [], "labels": []}
        for x in batch:
            ids = torch.tensor(x["input_ids"], dtype=torch.long)
            mask = torch.tensor(x["attention_mask"], dtype=torch.long)
            labels = torch.tensor(x["labels"], dtype=torch.long)
            n = maxlen - len(ids)
            out["input_ids"].append(torch.cat([ids, torch.full((n,), pad, dtype=torch.long)]))
            out["attention_mask"].append(torch.cat([mask, torch.zeros(n, dtype=torch.long)]))
            out["labels"].append(torch.cat([labels, torch.full((n,), -100, dtype=torch.long)]))
        return {k: torch.stack(v) for k, v in out.items()}

    training = TrainingArguments(
        output_dir=args.out,
        num_train_epochs=args.epochs,
        per_device_train_batch_size=args.batch,
        per_device_eval_batch_size=1,
        gradient_accumulation_steps=args.grad_accum,
        learning_rate=args.lr,
        lr_scheduler_type="cosine",
        warmup_steps=3,
        logging_steps=5,
        save_strategy="no",
        eval_strategy="no",
        bf16=True,
        gradient_checkpointing=True,
        optim="paged_adamw_8bit",
        report_to=[],
        remove_unused_columns=False,
        dataloader_num_workers=0,
    )
    trainer = Trainer(
        model=model, args=training, train_dataset=train_ds, eval_dataset=val_ds,
        data_collator=collate,
    )
    trainer.train()
    model.save_pretrained(args.out)
    tokenizer.save_pretrained(args.out)
    print(f"saved adapter -> {args.out}")


if __name__ == "__main__":
    main()
