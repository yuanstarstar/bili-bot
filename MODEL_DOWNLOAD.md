# 模型、运行时与打包

Git 仓库**不包含**大模型、LoRA、SenseVoice、llama.cpp、Python 运行时和 ffmpeg。原因是 GitHub 单文件/仓库大小限制，也为了避免把 6.5GB 二进制塞进历史。

你有两种方式：

1. 直接用 release 里的 `BiliBot-0.1.0-win64.zip`（已包含全部运行时与模型）。
2. 从源码跑：按下面准备模型/运行时，或者只接一个本地/远程 OpenAI 兼容服务。

---

## 1. Qwen3.5-4B GGUF

推荐文件：`Qwen3.5-4B-Q4_K_M.gguf`，2,740,937,888 bytes。

- ModelScope：  
  `https://modelscope.cn/models/unsloth/Qwen3.5-4B-GGUF`
- Hugging Face：  
  `https://huggingface.co/unsloth/Qwen3.5-4B-GGUF`

放到：

```text
models/Qwen3.5-4B-Q4_K_M.gguf
```

SHA256（当前本机校验值）：

```text
00FE7986FF5F6B463E62455821146049DB6F9313603938A70800D1FB69EF11A4
```

> 国内直连 Hugging Face 可能超时，ModelScope 通常更稳；也可以用 `hf-mirror.com`。

---

## 2. llama.cpp 运行时

打包版在 `runtime/llama/` 里带了 `llama-server.exe` 与 CUDA 运行库。

源码版二选一：

- 用你自己的 `llama-server.exe`，在设置里把 `llm.base_url` 指向它；
- 或从 llama.cpp release 下载 Windows CUDA 构建，放到 `runtime/llama/`，启动：

```bat
runtime\llama\llama-server.exe ^
  --model models\Qwen3.5-4B-Q4_K_M.gguf ^
  --host 127.0.0.1 --port 8081 ^
  -c 4096 -ngl 99 --jinja --no-webui
```

如果你用 Ollama：

```bat
ollama serve
ollama run qwen3.5:4b
```

设置里填 `http://127.0.0.1:11434/v1`，模型名以 `ollama list` 为准。

---

## 3. 评论 LoRA（可选）

当前 release 带 `qwen3.5-4b-lora-devil.gguf`（约 84.9MB）。源码仓库不提交它；如果你有自己的 adapter：

```bat
py -3 tools\llama.cpp\llama.cpp-master\convert_lora_to_gguf.py ^
  --base models\Qwen3.5-4B-hf ^
  --outfile models\qwen3.5-4b-lora-devil.gguf ^
  data\train\adapter_mega_c
```

然后设置：

```json
"llm": { "adapter": "models/qwen3.5-4b-lora-devil.gguf" }
```

`app/launch.py` 会自动给 llama-server 加 `--lora`；置空则用底座。

---

## 4. SenseVoice（本地语音识别）

模型目录结构：

```text
runtime/models/sensevoice/
  sensevoice-onnx/
    model.int8.onnx
    tokens.txt
  silero/
    silero_vad.onnx
```

来源：

- FunAudioLLM / SenseVoice：`https://github.com/FunAudioLLM/SenseVoice`
- sherpa-onnx 模型：`https://github.com/k2-fsa/sherpa-onnx`

依赖：

```bat
runtime\python\python.exe -m pip install sherpa-onnx onnxruntime numpy
```

源码版用 `py -3 -m pip install -r requirements-asr.txt`。

配置：

```json
"voice": {
  "enabled": true,
  "ffmpeg": "runtime/bin/ffmpeg.exe",
  "asr": {
    "enabled": true,
    "model_dir": "runtime/models/sensevoice"
  }
}
```

`ffmpeg` 也可留空，程序会在 PATH 里找。

---

## 5. 打包

打包版目录（`dist/BiliBot`）需要：

```text
app/
personas/
train/
tests/                 （可选）
config.json
config.example.json
README.md
PROJECT_OVERVIEW.md
start.bat
models/Qwen3.5-4B-Q4_K_M.gguf
models/qwen3.5-4b-lora-devil.gguf
runtime/python/
runtime/llama/
runtime/models/sensevoice/
runtime/bin/ffmpeg.exe
data/                  （空目录）
```

打包前务必清空 `data/`，确认没有 `bili_cookie.json`、`bot.sqlite3`、日志、训练数据。

用项目自带脚本（STORE 模式，避免重复压缩 2.7GB 模型）：

```bat
py -3 _pack.py
```

输出：`dist/BiliBot-0.1.0-win64.zip`。

---

## 6. 许可证

模型/运行时的许可证见 `THIRD_PARTY_NOTICES.md`；不要把模型权重提交到 Git。
