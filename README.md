# BiliBot · 本地 B 站小号机器人

在本机跑一个有人味的 B 站小号：扫码登录、立人设、自己刷推荐/热门、挑视频、解析视频与评论、发首评、楼中楼、回私信、按口味 @ 主人、限速与熔断、清理缓存。模型走本机 OpenAI 兼容接口（llama.cpp / Ollama 等），不依赖云端。

> 当前版本：**0.1.0**。扫码登录、推荐/热门、解析、生成、首评、楼中楼、私信回复、@主人、限速、熔断、控制台，以及内置 Qwen3.5-4B、SenseVoice 听视频都已落地。评论公开可见性已用主号在真实视频验证（`state=0`），并加了 `Origin`/`Referer` 与影子封禁检测。

> **开源说明**：仓库只放代码，不含模型、运行时、Cookie、数据库与打包 zip。完整介绍见 [PROJECT_OVERVIEW.md](PROJECT_OVERVIEW.md)，模型下载见 [MODEL_DOWNLOAD.md](MODEL_DOWNLOAD.md)，发布前安全检查见 [SECURITY.md](SECURITY.md)，许可证见 [LICENSE](LICENSE)。

## 快速开始（打包版）

1. 解压 `BiliBot-0.1.0-win64.zip` 到任意目录（路径别太深，别放中文括号）。
2. 双击 `start.bat`。它会先拉起内置 Qwen3.5-4B（8081），再开控制台；首次约 10–30 秒。
3. 浏览器自动打开 `http://127.0.0.1:8765`（端口被占用会 +1，控制台会打印实际地址）。
4. 首次启动会弹 **5 步向导**：检查模型/登录/语音 → 扫码 → 人设 → 主人 UID/节奏 → 完成。也可以点右上角「向导」重开。
5. 「概览」页先点 **单跑一轮（dry-run）**，去「草稿」页看结果；确认后再点 **开始自动运行**。
6. 设置页的 **发布审核** 可选「自动发布」或「先审后发」：先审后发时，首评和楼中楼都进草稿箱，确认后才发。

> 打包版自带 `runtime/python`、`runtime/llama`、`models/Qwen3.5-4B-Q4_K_M.gguf`、SenseVoice 模型和 `runtime/bin/ffmpeg.exe`，不需要另装 Python 或模型。首次登录会把旧项目的 cookie 复制一份到本包 `data/`，不改原文件。

## 快速开始（源码开发）

需要 **Windows + Python 3.10+**（本机用 `py -3`；不要用应用商店的 `python` 别名）。

```bat
cd /d D:\bilibili\bili-bot
py -3 app\main.py
```


## 接入本地模型

机器人只要求一个 OpenAI 兼容的 `/v1/chat/completions`。打包版已经内置 Qwen3.5-4B（Q4_K_M，约 2.7GB）和 llama.cpp CUDA 运行时，`start.bat` 会自动拉起，默认：

- 接口地址：`http://127.0.0.1:8081/v1`
- 模型名：`qwen3.5-4b`

手动启动/换模型：

```bat
runtime\llama\llama-server.exe -m models\Qwen3.5-4B-Q4_K_M.gguf --host 127.0.0.1 --port 8081 -c 4096 -ngl 99 --jinja
```

设置里填对应地址与模型名即可。Ollama 也可以：`ollama serve` 后填 `http://127.0.0.1:11434/v1`，模型名以 `ollama list` 为准。

> 4B 模型只看文字：标题、简介、标签、字幕、热弹幕、高赞评论。它看不了画面，所以 `字幕/弹幕/评论/语音转录` 的质量决定评论质量。


## 语音识别（可选）

- **听视频**：本地 SenseVoice（`sherpa-onnx`），不占 GPU、不联网。模型默认找 `~/.dsh/speech-to-text/sensevoice/models`，也支持 `runtime/models/sensevoice`。视频没有字幕时，引擎会自动抓前 N 秒音轨转文字，再交给模型写评论。需要 `ffmpeg` 在 PATH，或配置 `voice.ffmpeg` 指向 exe。
- **配置**：`voice.asr.model_dir` 指向模型目录；`voice.asr.in_dry_run` 默认 `false`（dry-run 不跑语音，省时间），想看效果可临时改 `true`。
- **依赖**：`onnxruntime`、`sherpa-onnx`、`numpy`、`ffmpeg`。打包版会把这些一起塞进 `runtime/` 和 `bin/`。
- 本版本不包含语音合成（TTS）；语音只用于“听懂视频”，不生成音频、不发 B 站。

## 它怎么“像人”

- **人设 prompt**：`personas/default.json` 决定身份、兴趣、语气、长度、口头禅、雷点。
- **三个钩子**：先从视频里抽“槽点/梗/信息点/情绪点”，再写评论，避免空泛夸。
- **风格 RAG**：从已抓到的同标签高赞评论里取 8 条做 few-shot。
- **三条候选 + 自评**：具体性、口语度、AI 味、冒犯风险，选一条。
- **硬过滤**：长度、禁词（首先/其次/总之/不得不说/综上所述/希望对你有帮助…）、链接、AI 自曝、@ 权限。
- **simhash 去重**：和最近发过的太像就丢弃。
- **节奏**：默认评论 8/日、楼中楼 15/日、私信 5/日；写操作间隔 20–60 分钟；活跃 09:00–12:00、14:00–23:30；看 3–8 个视频才评论 1 条；评论前按视频时长停留 30%–80%（上限 8 分钟），再读 10–60 秒评论。
- **风控**：遇到 `-352/-412/-509/-799/86038` 或连续失败自动熔断 6 小时；被吞的评论 15 分钟后标记 `unverified`。

## 目录

```
bili-bot/
  app/
    bili/        B 站接口（客户端、WBI、扫码登录、推荐、评论、字幕、弹幕、私信、消息中心）
    brain/       LLM、人设、prompt、生成、质检、风格 RAG
    robot/       引擎、限速、熔断、动作封装
    voice/       SenseVoice 听视频（仅 ASR）
    web/         本地控制台（server.py + static/）
    config.py    配置默认值与深合并
    store.py     SQLite（动作、视频、评论、草稿、私信、去重）
    main.py      入口
  personas/default.json
  data/          运行后生成：bot.sqlite3、bili_cookie.json、logs/、cache/
  config.json    首次运行自动生成（可对照 config.example.json）
  tests/         核心冒烟测试
```

## 常用命令

```bat
py -3 app\main.py                  :: 启动控制台
py -3 app\main.py --no-open        :: 不自动开浏览器
py -3 app\main.py --port 9000      :: 指定端口
py -3 app\main.py --once           :: 单跑一轮 dry-run，打印 JSON
py -3 app\main.py --once --live    :: 单跑一轮并真实发布（谨慎）
py -3 -m unittest discover -s tests -v
```

## 训练（可选）

采集/构造/评测（数据默认剔除 2026 年 10 月）：

```bat
py -3 train\collect.py --videos 140 --months 2025-11..2026-09 ^
  --comment-pages 4 --min-like 20 --min-video-reply 300 --min-video-play 150000 ^
  --order click --workers 6 --out data\train\raw_mega.jsonl
py -3 train\build_sft.py --raw data\train\raw_mega.jsonl --out-dir data\train\sft_mega ^
  --val-ratio 0.08 --min-target-like 30 --min-nested-like 5
py -3 train\eval.py --sft data\train\sft_mega --limit 120 --out data\train\eval_mega.json
```

`train/collect.py` 默认剔除 2026-10，按视频播放/评论数过滤高互动视频，评论用热度排序（mode=3），搜索用游客 client（登录态搜索易被风控返回 0）；`build_sft.py` 生成首评/楼中楼/私信三类 ShareGPT 数据；`eval.py` 出 AI 腔率、长度、distinct-2、重复率、与真实评论的 bigram 重合度，并生成人工盲测 HTML。

QLoRA 训练脚本：`train\train_qlora.py`（transformers 5.21 + peft + bitsandbytes，4bit，8GB 保守参数）。Qwen3.5-4B 是多模态模型，需要 CUDA 版 torch；8GB 下若 OOM，把 `--max-len` 降到 384 或把 `--grad-accum` 加到 24，仍然不行就租 24GB 卡。

本机“魔鬼化”版本使用 139 个高互动视频、3874 条高赞评论（点赞中位数 226、p90 1701）、6520 条楼中楼；构造 SFT 4152 训练 + 310 验证（A 1885 / B 2238 / C 339）。四个训练变体 + 底座，在 117 条共同验证集上的结果：

| 版本 | avg_len | distinct-2 | 完全重复 | 与真实评论 bigram 重合 | score |
|---|---:|---:|---:|---:|---:|
| 底座 4B | 18.56 | 0.5226 | 0 | 0.0111 | 1.6233 |
| A 2ep lr2e-4 r16 | 19.84 | 0.6257 | 0.0171 | 0.0320 | 1.9003 |
| B strict 2ep lr1e-4 r32 | 17.90 | 0.6576 | 0.0256 | 0.0231 | 1.8835 |
| C 3ep lr1e-4 r16 | 24.08 | 0.6315 | 0.0085 | 0.0306 | **1.9795** |
| D 2ep lr1.5e-4 r16 | 20.27 | 0.6200 | 0.0085 | 0.0296 | 1.9400 |

**选中 C**：`models\qwen3.5-4b-lora-devil.gguf`（84.9MB，由 `data\train\adapter_mega_c` 转成 llama.cpp LoRA）。C 比底座明显更多样、更接近评论语气，完全重复率 0.85%，AI 腔率 0，但长度偏长（24 字 vs 底座 18.6 字），事实性仍受 4B 限制。**当前 `llm.adapter` 默认指向这版**；想回底座就把 `llm.adapter` 置空，`app/launch.py` 会自动不加 `--lora`。

完整训练记录见 `data\train\night_report.md`，自动化脚本 `train\night_run.py`（采集→SFT→训练/转 GGUF/评测→选最佳），D 变体记录了加时赛结果但未超过 C。要更稳，建议扩到 2k–5k 个视频、用更大卡再训；验证集只有 117 条，指标只作相对参考。

## 配置要点（config.json）

| 键 | 说明 |
|---|---|
| `llm.base_url` / `llm.model` | 本地模型地址与名字 |
| `bili.cookie_file` | 留空用 `data/bili_cookie.json`；也可指向已有 cookie |
| `bot.comment_per_day` | 每日首评上限 |
| `bot.nested_per_day` | 每日楼中楼上限 |
| `bot.dm_per_day` | 每日私信上限（来信回复 + 主动） |
| `bot.min_interval_min` / `max_interval_min` | 两次写操作之间的随机间隔 |
| `bot.active_hours` | 活跃时段，支持逗号分隔多段 |
| `bot.comment_probability` | 看完一个视频后评论的概率 |
| `bot.approval_mode` | `auto` 自动发布 / `review` 先审后发（进草稿箱） |
| `bot.use_recommend` | 默认 `true`：刷 B 站首页个性化推荐；热门/排行只作兜底 |
| `bot.use_search` | 是否叠加人设关键词搜索（默认关，避免变热门检索） |
| `bot.simulate_dwell` | 是否真的按视频时长等待（调试可关） |
| `bot.at_master` | `mode` 取 `off/taste/always`；`uid`、`name`、`taste`、`daily_limit`（0=不限，默认 8）；@ 后缀不计入评论 6–30 字上限 |
| `bot.dm` | `reply_incoming`、`proactive`（`off/reply/whitelist`）、`whitelist`、`proactive_daily` |
| `risk.*` | 风控码、熔断时长、每日总动作上限 |
| `voice.asr.model_dir` | SenseVoice 模型目录（可留空自动找） |
| `voice.asr.in_dry_run` | dry-run 是否也跑语音转录，默认 false |
| `train.exclude_ranges` | 训练数据剔除区间，默认 `2026-10-01 ~ 2026-11-01` |

## 安全与边界

- 控制台只绑 `127.0.0.1`，不要改成 `0.0.0.0` 暴露到公网。
- Cookie 用 Windows DPAPI 加密存 `data/bili_cookie.json`；不要把这个文件发给别人。
- **用小号，不要用主账号。** 自动评论、主动私信都违反 B 站社区/接口预期，存在封号风险；私信风险最高。
- 默认全自动发布，但建议第一周把 `comment_per_day` 调到 2–3、`dm.proactive` 设为 `off`，先观察是否被吞、是否被限流。
- 机器人不训练、不上传你的数据；训练数据只在本机。

## 还没做完的

- 私信发送已实测成功（主人账号收到过测试消息）；默认只建议用来信回复，主动私信务必设白名单并把 `bot.dm.proactive` 设为 `whitelist`。
- 训练：数据管线（采集/构造/评测/QLoRA/GGUF 转换）已跑通，C 变体 LoRA 已默认启用；139 个视频/4152 条训练样本仍偏小，建议扩到 2k–5k 视频再训，并关注事实性（4B 对视频细节仍会编）。
- 字幕接口部分视频需要登录/权限，拿不到会退化为标题+标签+评论+语音转录。
- B 站语音私信/语音评论接口未验证；本版本不含语音合成（TTS）。
- 首启向导已可用；扫码登录在「登录」页或向导第 2 步完成。
- 账号级阿瓦隆/影子封禁不是程序能完全解决的；换号、养号、降低频率才是根本办法。


训练数据口径：剔除 **2026-10-01 至 2026-10-31** 整个月，其它年份的 10 月保留；配置在 `train.exclude_ranges`。
