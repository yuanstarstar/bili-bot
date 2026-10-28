# 训练数据与微调（Qwen3.5-4B）

目标：让 4B 学会 B 站评论区的说话方式，而不是通用 AI 腔。

## 1. 采数据

```bat
py -3 train\collect.py --videos 200 --months 2025-11..2026-09 ^
  --pages 2 --comment-pages 2 --min-like 2 --max-nested 8 ^
  --workers 6 --order pubdate --out data\train\raw.jsonl
```

- 默认剔除 `2026-10` 整月（`--exclude-month ""` 可关闭，仅用于调试）。
- 每条视频保留：标题/简介/分区/标签/播放数据、最多 50 条高赞评论、最多 8 条楼中楼。
- 过滤：广告/求三连/URL/纯表情/无中文/UP 主自评。
- 搜索用 `pubdate` 排序，比 `click` 更容易拿到评论时间在目标区间内的视频；但热评仍可能集中在最近，出现「评论=0」是正常的，换更老月份或降低 `--min-like`。

## 2. 构造 SFT

```bat
py -3 train\build_sft.py --raw data\train\raw.jsonl --out data\train\sft
```

任务：
- A 首评：视频上下文 → 一条评论。
- B 楼中楼：根评论 + 被回复内容 → 一条回复（要求不另起顶层）。
- C 私信：对方消息 + 最近对话 → 一条回复。

输出 `train.jsonl` / `val.jsonl`（ShareGPT 格式）与 `stats.json`。按 bvid 划分 train/val，避免同视频泄漏；simhash 去重。

## 3. QLoRA（8GB 显存）

Qwen3.5-4B 是**多模态**模型（`Qwen3_5ForConditionalGeneration`），文本部分 `qwen3_5_text`，需要 `transformers 5.21+`。8GB 下建议：

- 4bit QLoRA，`r=16, alpha=32, dropout=0.05`
- `max_length=1024`，`per_device_batch_size=1`，梯度累积 8–16
- gradient checkpointing 开
- 只训 `q_proj/k_proj/v_proj/o_proj/gate_proj/up_proj/down_proj`
- 先训任务 A（首评），再混 B/C；A 的数据量最大

若本地 OOM 或依赖装不上，两条退路：
1. 租 24GB 显卡（4090）跑同样的 LLaMA-Factory/peft 脚本；
2. 改用 llama.cpp LoRA（需要 `llama-finetune.exe`，当前内置包只有 server/quantize）。

## 4. 评测

```bat
py -3 train\eval.py --sft data\train\sft --limit 30 --out data\train\eval.json --blind data\train\blind.html
```

指标：AI 腔词率、平均长度、标点/emoji 率、distinct-2、完全重复率、与真实评论的 bigram 重合度；`--blind` 生成人工盲测 HTML。当前 4B 基线的 `distinct_2` 约 0.69，真实评论约 0.94，说明**重复度还偏高、句式偏单一**，这正是微调要补的。

- 盲测：人分不清 AI/真人比例（目标 <40% 被认出）
- self-BLEU / 长度分布 / 标点分布
- 违规词率、被吞率、被举报率（小流量试跑）
- 上线后看点赞/回复弱信号，不要只看生成速度

## 5. QLoRA

`train/train_qlora.py` 已写好：4bit nf4、LoRA r=16/alpha=32、梯度检查点、paged_adamw_8bit、batch1+梯度累积。需要 CUDA 版 torch、`transformers 5.21+`、`peft`、`bitsandbytes`。

8GB 显存建议先 `--max-len 768 --grad-accum 24`；仍 OOM 就租 24GB 卡。训练出的 PEFT adapter 需要用 llama.cpp 的 `convert_lora_to_gguf.py` 转成 GGUF，放到 `models/adapter.gguf`，再把 `config.json` 的 `llm.adapter` 指向它；打包版的 `app/launch.py` 会自动加 `--lora` 启动。

## 6. 口径

训练数据剔除 **2026-10-01 至 2026-10-31** 整个月；其它年份的 10 月保留。配置在 `config.json` 的 `train.exclude_ranges`。
