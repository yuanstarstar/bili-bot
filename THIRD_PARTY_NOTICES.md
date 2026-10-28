# 第三方组件与模型声明

本项目代码以 MIT 许可证发布（见 `LICENSE`）。以下组件/模型各自遵循其上游许可证；本项目不重新授权它们。

## 模型

| 组件 | 用途 | 上游 |
|---|---|---|
| Qwen3.5-4B（GGUF） | 本地推理 / 生成 | https://huggingface.co/Qwen/Qwen3.5-4B |
| Qwen3.5-4B 评论 LoRA | 可选 adapter | 本项目训练产物；底座仍受 Qwen 许可证约束 |
| SenseVoice（ONNX） | 本地语音识别 | https://github.com/FunAudioLLM/SenseVoice |
| silero-vad | 语音活动检测 | https://github.com/snakers4/silero-vad |

模型权重的许可证以各自仓库的 `LICENSE`/`README` 为准；如用于商业场景，请自行核对。

## 运行时与库

| 组件 | 用途 | 许可证（上游） |
|---|---|---|
| llama.cpp / llama-server | 本地 GGUF 推理 | MIT |
| Python embeddable | 打包运行时 | PSF License |
| sherpa-onnx | SenseVoice 推理 | Apache-2.0 |
| onnxruntime | ONNX 推理 | MIT |
| numpy | 数值计算 | BSD-3-Clause |
| ffmpeg | 音轨抽取 | LGPL/GPL（取决于构建） |
| qrcodejs | 登录二维码 | MIT |
| SQLite | 本地数据库 | Public Domain |

## B 站接口

本项目使用 B 站 Web 端公开/半公开接口，**与哔哩哔哩无官方关联，也未获得官方授权**。接口可能随时变更；使用本项目产生的账号风险由使用者自行承担。

## 字体/图标/图片

控制台不包含第三方字体或商业图标；二维码由 qrcodejs 在浏览器本地生成。

## 免责声明

本项目按“现状”提供，不保证不被限流、折叠、阿瓦隆或封号。请遵守 B 站用户协议与当地法律，仅用于学习与个人研究。
