# BiliBot 项目总览

> 一个在本机运行的 B 站小号机器人：人类扫码登录、立人设、自己刷视频、理解视频与评论、发首评、楼中楼、回私信、按口味 @ 主人；模型、语音识别、控制台都在本地。

- **当前版本**：0.1.0
- **运行平台**：Windows 10/11（打包版）；源码版需要 Python 3.10+
- **模型**：Qwen3.5-4B（GGUF Q4_K_M）+ 可选 LoRA adapter
- **语音**：本地 SenseVoice（sherpa-onnx），只做“听懂视频”，不做 TTS
- **数据**：SQLite + 本地文件；B 站 Cookie 用 Windows DPAPI 加密
- **控制台**：`http://127.0.0.1:8765`，只绑本机
- **许可证**：MIT（模型、运行时与第三方组件另见 `THIRD_PARTY_NOTICES.md`）

> 这不是一个“自动刷量”工具。它按人的节奏看视频、读评论、偶尔发言，并对自己能看到的互动做延迟回复。B 站没有公开的机器人接口，自动评论/私信存在被限流、被折叠、被阿瓦隆（仅自己可见）甚至封号的风险，请只用小号，风险自负。

---

## 1. 它是什么

BiliBot 把“刷 B 站”拆成四层：

1. **接入层 `app/bili/`**：纯标准库实现 B 站 Web 接口，包括扫码登录、WBI 签名、buvid、推荐/热门/排行、视频详情、字幕、弹幕、评论、楼中楼、私信、消息中心。
2. **大脑层 `app/brain/`**：OpenAI 兼容的本地 LLM 客户端、人设、prompt、视频钩子、三候选生成、自评、硬过滤、风格 RAG、simhash 去重。
3. **行为层 `app/robot/`**：状态机、选视频、拟人停留、限速、每日配额、熔断、首评/楼中楼/私信的动作封装、回复巡检。
4. **界面与数据层**：本地 Web 控制台、SQLite、文件缓存、DPAPI 加密 Cookie、日志。

```text
B 站 Web API
   │
   ├─ app/bili/       登录 / 推荐 / 视频 / 字幕 / 弹幕 / 评论 / 私信
   │
   ├─ app/robot/      选视频 → 理解 → 拟人停留 → 生成 → 发布 → 巡检
   │        │
   │        ├─ app/brain/   LLM + 人设 + prompt + 质检
   │        └─ app/voice/   SenseVoice 语音转写
   │
   └─ app/web/        127.0.0.1 控制台 + 草稿箱 + 日志
```

---

## 2. 当前已实现

### 账号与登录

- 人类用 B 站 App 扫码登录，自动保存 Cookie。
- Cookie 用 Windows DPAPI 加密，写在 `data/bili_cookie.json`；只在本机可解密。
- 检测到其它项目的旧 Cookie 时，会**复制一份**到本实例，原文件只读，不共用。
- 不改注册表，不写系统目录。

### 刷视频与理解

- 默认拉 B 站首页个性化推荐（`/x/web-interface/wbi/index/top/feed/rcmd`），热门/排行只作兜底。
- 支持指定 BV 测试：输入 BV 号，只生成草稿、不发布，返回视频上下文、标签、字幕字数、弹幕数。
- 视频理解输入：标题、简介、UP、分区、时长、标签、字幕、热弹幕、高赞评论；没有字幕时可用 SenseVoice 转写音轨。
- 4B 模型看不了画面，画面信息只能通过字幕/弹幕/评论/语音间接获得。

### 评论与楼中楼

- 首评：先抽“槽点/梗/信息点/情绪点”，再生成 3 条候选，自评后选 1 条；带长度、禁词、链接、AI 自曝、重复度检查。
- 楼中楼：`root` 指向根评论、`parent` 指向被回复评论，保证回复在楼内，不另起顶层。
- 回复上下文：原视频缓存 + 本层楼当前回复之前的根评论/历史回复（最多 10 条），prompt 要求承接前文。
- 评论请求带同源 `Origin` 与视频页精确 `Referer`；这是公开可见的关键修复之一。
- 能识别 `state=17`（阿瓦隆/仅自己可见），标记 `shadow`，不把它当成公开评论。

### 私信

- 默认只回复来信；主动私信只发白名单，且每日限额。
- 会话上下文：B 站接口最近 40 条 + 本地最近 200 条，去重后取最近 24 条；prompt 展示最近 20 条。
- 私信不再和评论共用 20 分钟全局间隔；评论/楼中楼一组、私信一组。
- 对“想死吗 / 去死 / 杀了你 / 傻逼 / 贱人”等威胁辱骂做硬拦截；prompt 要求不升级冲突。
- 已实测发送成功；API 返回 `ok=True` 且有 `msg_key` 才算成功。

### 节奏、限速与熔断

- 默认评论 8/日、楼中楼 15/日、私信 5/日；写操作间隔 20–60 分钟。
- 活跃时段默认 09:00–12:00、14:00–23:30。
- 浏览 3–8 个视频才评论 1 条；评论前按视频时长停留 30%–80%（上限 8 分钟），再读 10–60 秒评论。
- 每日配额为 0 表示**关闭该类型自动写入**，不是“不限”。
- 草稿批准也遵守限速/配额；手动 force 可以跳过活跃时段和配额，但仍受最小间隔约束。
- 遇到 `-352/-412/-509/-799/86038` 等风控码、或连续失败，自动熔断 6 小时；`12006/12002`（评论已删除）不再累计熔断。
- 账号级阿瓦隆/影子封禁不是程序能完全解决的；换号、养号、降低频率才是根本办法。

### 控制台

- 概览：状态、今日计数、模型/登录/语音、推荐预览、指定 BV 测试、开始/暂停/停止/单跑。
- 登录：二维码、轮询、当前账号。
- 人设：JSON 编辑、保存、恢复默认。
- 设置：模型地址、人设、审核模式、评论/楼中楼/私信配额、间隔、@主人、白名单、缓存清理。
- 草稿：自动/先审后发；楼中楼草稿也进这里。
- 语音：SenseVoice 状态、指定 BV 试听/转写。
- 日志：运行日志、最近动作、缓存信息。

---

## 3. 目录

```text
bili-bot/
  app/
    bili/           B 站接口：client/auth/login/feed/video/comments/subtitle/danmaku/dm/msgfeed
    brain/          LLM、人设、prompt、生成、质检、风格 RAG
    robot/          engine、rate、circuit、actions
    voice/          SenseVoice ASR（仅语音识别）
    web/            server.py + static/（控制台）
    config.py       默认配置与深合并
    store.py        SQLite
    secure.py       Windows DPAPI
    main.py         源码入口
    launch.py       打包版入口（先拉起 llama-server）
  personas/
    default.json    人设示例
  train/
    collect.py      采集高互动视频与评论
    build_sft.py    构造首评/楼中楼/私信 SFT
    eval.py         自动评测 + 盲测 HTML
    train_qlora.py  Qwen3.5-4B 4bit QLoRA
    night_run.py    训练流水线（可选）
  tests/
    test_core.py    10 个核心单测
  config.example.json
  start.bat
  README.md
  PROJECT_OVERVIEW.md
  SECURITY.md
  THIRD_PARTY_NOTICES.md
```

运行时生成（不进 Git）：

```text
data/
  bot.sqlite3        动作、视频、评论、草稿、私信、已看视频
  bili_cookie.json   DPAPI 加密的登录态
  logs/              日志
  cache/             下载缓存
  train/             采集与训练数据
  voice/             ASR 临时文件
```

---

## 4. 快速开始

### 4.1 打包版（推荐给普通用户）

1. 解压 `BiliBot-0.1.0-win64.zip` 到任意目录，路径尽量短、不要中文括号。
2. 双击 `start.bat`。脚本会先检查/拉起内置 Qwen3.5-4B（8081），再启动控制台。
3. 浏览器打开 `http://127.0.0.1:8765`（端口被占用会顺延，控制台会打印实际地址）。
4. 按首启向导完成：环境检查 → 扫码 → 人设 → 主人 UID/节奏 → 完成。
5. 先在「概览」点“单跑 dry-run”，去「草稿」看结果；确认后再“开始自动运行”。

打包版自带：

- `runtime/python`（嵌入式 Python，含 sherpa-onnx / onnxruntime / numpy）
- `runtime/llama`（llama.cpp + CUDA 运行库）
- `models/Qwen3.5-4B-Q4_K_M.gguf`
- `models/qwen3.5-4b-lora-devil.gguf`（高质量评论 LoRA，可选置空）
- `runtime/models/sensevoice`（SenseVoice ONNX + tokens + silero VAD）
- `runtime/bin/ffmpeg.exe`
- 空 `data/`（不含任何人的 Cookie/数据库）

### 4.2 源码版

```bat
cd /d <你的目录>\bili-bot
py -3 app\main.py --no-open --port 8765
```

需要 Python 3.10+；不要用 Windows 应用商店的 `python` 别名，用 `py -3` 或真实 python.exe。

源码版不包含大模型、llama.cpp、SenseVoice 权重、ffmpeg；按 `MODEL_DOWNLOAD.md` 准备，或在设置里接 Ollama / 其它 OpenAI 兼容服务。

---

## 5. 模型与语音

### 5.1 推理模型

- 默认：`Qwen3.5-4B-Q4_K_M.gguf`，约 2.74GB。
- 启动：`runtime\llama\llama-server.exe -m models\Qwen3.5-4B-Q4_K_M.gguf --host 127.0.0.1 --port 8081 -c 4096 -ngl 99 --jinja --no-webui`
- 接口：`http://127.0.0.1:8081/v1`，模型名 `qwen3.5-4b`。
- `llm.disable_thinking=true`：Qwen3 系默认 thinking 会让 content 为空，必须关闭。
- `llm.adapter` 可指向 LoRA GGUF；`app/launch.py` 会自动给 llama-server 加 `--lora`。置空则回底座。

### 5.2 SenseVoice（本地语音识别）

- 只做 ASR：视频没有字幕时，抓前 N 秒音轨转文字，再交给 LLM 写评论。
- 模型目录：`runtime/models/sensevoice`（打包版）；源码版默认找 `~/.dsh/speech-to-text/sensevoice/models`。
- 依赖：`sherpa-onnx`、`onnxruntime`、`numpy`、`ffmpeg`。
- 不包含 TTS：本项目不生成语音、不发语音私信。
- 视频音轨抽帧需要带 B 站 Cookie 的 ffmpeg headers；`ASR.transcribe_video` 已处理，失败会回退。
- 只读前 120 秒（可配置），避免长视频拖慢。

### 5.3 训练（可选）

- 数据口径：默认剔除 **2026-10-01 至 2026-10-31**，其它年份 10 月保留。
- 采集：高互动视频（播放/评论阈值）+ 热度排序评论（mode=3），过滤广告/URL/AI 腔。
- SFT：任务 A 首评、B 楼中楼、C 私信回复，ShareGPT 格式，按 bvid hash 划分 train/val。
- QLoRA：4bit nf4、r=16/32、gradient checkpointing、batch 1 + 梯度累积；8GB 显存可跑小样本，建议更大卡。
- 评测：avg_len、distinct-2、exact_dup、与真实评论 bigram 重合、AI 腔率、盲测 HTML。
- 当前“魔鬼化”数据集：139 个视频、3874 条评论、6520 条楼中楼；SFT 4152/310；C 变体在 117 条共同验证集上 score 1.9795（底座 1.6233）。
- 具体命令见 `train/README.md` 和 `README.md`。

---

## 6. 配置与数据

- `config.example.json` 是公开模板；`config.json` 是个人配置，**不进 Git**。
- Cookie 默认在 `data/bili_cookie.json`；也可用 `bili.cookie_file` 指定。
- SQLite 在 `data/bot.sqlite3`；删除 `data/` 会清空登录、动作、草稿、私信记录。
- 控制台只绑 `127.0.0.1`；不要改成 `0.0.0.0` 暴露到公网。
- 日志默认 `data/logs/`；打包版可一键清理缓存，但不会删除登录态（除非手动删 `data/bili_cookie.json`）。

---

## 7. 安全、隐私与合规

- **只建议用小号。** 自动评论、主动私信都不是 B 站公开 API 的正常用法，存在风控、阿瓦隆、封号风险。
- **不要提交 Cookie、`config.json`、`data/`、日志。** 仓库的 `.gitignore` 已默认排除；发布前请再跑一遍 `scripts/check_secrets.py`（见 `SECURITY.md`）。
- Cookie 使用 Windows DPAPI，换机器/换用户通常无法解密；这是本机保护，不是加密备份。
- 控制台没有远程鉴权，只适合本机使用；不要把端口映射到公网。
- 训练数据只在本机；仓库不包含任何 B 站用户数据、模型权重、登录态。
- 评论内容可能被 B 站判定为垃圾/违规；发布前可用 `approval_mode=review` 先审后发。
- 语音识别会把视频音轨临时写入 `data/voice/`，失败会清理；不会上传云端。

---

## 8. 已知限制

- 4B 模型的事实性有限，可能把视频里没说的东西写进评论；prompt/上下文只能降低概率。
- 4B 看不了画面；纯音乐、无字幕、无语音的视频理解质量会明显下降。
- 推荐流跟随登录账号画像；新小号冷启动时推荐偏泛热门，需要刷一段时间。
- 评论公开可见性受账号信用、内容、请求头、频率共同影响；`state=17` 只能检测，不能保证解除。
- 私信接口是 Web 协议逆向，B 站改版可能失效。
- 训练集 139 个视频仍偏小；117 条验证集指标只作相对参考。
- 打包版约 6.5GB，主要是模型、CUDA 运行库、Python 与 ASR 模型；首次启动 10–30 秒。

---

## 9. 开源与第三方

- 本项目代码：MIT，见 `LICENSE`。
- 大模型、LoRA、SenseVoice、llama.cpp、ffmpeg、Python 运行时、sherpa-onnx、onnxruntime 等按各自许可证分发，见 `THIRD_PARTY_NOTICES.md`。
- 仓库不包含模型权重、打包 zip、Cookie、数据库；release 包单独提供。
- 发布到 GitHub 前请阅读 `SECURITY.md`，并确认 `git status` 里没有 `data/`、`config.json`、`dist/`、`models/`、`runtime/`。

---

## 10. 版本

- 0.1.0：扫码登录、推荐/热门、视频理解、首评、楼中楼、私信、@主人、限速/熔断、控制台、首启向导、SenseVoice ASR、Qwen3.5-4B + 评论 LoRA、训练/评测脚本。
- 打包文件与 SHA256 以 release 页/`dist` 目录为准；源码提交不包含二进制。
