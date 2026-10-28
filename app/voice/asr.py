# -*- coding: utf-8 -*-
"""本地 SenseVoice ONNX 语音识别（sherpa-onnx）。

用途：B 站视频没有字幕时，抓一段音频转成文字，喂给评论生成器。
模型目录默认 ~/.dsh/speech-to-text/sensevoice/models，也支持 runtime/models/sensevoice。
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import threading
import wave
from pathlib import Path
from typing import Any, Optional

from app.config import ROOT

_TAG_RE = re.compile(r"<\|[^|]*\|>")


class ASR:
    def __init__(self, cfg) -> None:
        self.cfg = cfg
        self._lock = threading.RLock()
        self._rec = None
        self._loaded_key = ""
        self.last_error = ""

    # ------------------------------------------------------------------ 路径
    def _models_root(self) -> Path:
        raw = str(self.cfg.get("voice.asr.model_dir") or "").strip()
        candidates: list[Path] = []
        if raw:
            p = Path(os.path.expandvars(os.path.expanduser(raw)))
            candidates.append(p if p.is_absolute() else ROOT / p)
        candidates += [
            Path.home() / ".dsh" / "speech-to-text" / "sensevoice" / "models",
            ROOT / "runtime" / "models" / "sensevoice",
            ROOT / "models" / "sensevoice",
        ]
        for p in candidates:
            if p.is_dir() and self._find(p):
                return p
        return candidates[0]

    def _find(self, base: Path) -> Optional[tuple[Path, Path]]:
        model_candidates = [
            base / "sensevoice-onnx" / "model.int8.onnx",
            base / "model.int8.onnx",
            base / "model.onnx",
        ]
        token_candidates = [
            base / "sensevoice-onnx" / "tokens.txt",
            base / "tokens.txt",
        ]
        model = next((p for p in model_candidates if p.is_file()), None)
        tokens = next((p for p in token_candidates if p.is_file()), None)
        if model and tokens:
            return model, tokens
        return None

    def check(self) -> tuple[bool, str]:
        try:
            import sherpa_onnx  # noqa: F401
        except Exception as e:  # noqa: BLE001
            return False, f"缺少 sherpa-onnx：{e}"
        found = self._find(self._models_root())
        if not found:
            return False, f"找不到 SenseVoice 模型：{self._models_root()}"
        return True, f"SenseVoice 就绪（{found[0].name}）"

    def available(self) -> bool:
        return self.check()[0]

    # ------------------------------------------------------------------ 识别
    def _recognizer(self):
        found = self._find(self._models_root())
        if not found:
            raise RuntimeError(f"找不到 SenseVoice 模型：{self._models_root()}")
        model, tokens = found
        key = f"{model}|{tokens}"
        if self._rec is not None and self._loaded_key == key:
            return self._rec
        import sherpa_onnx

        self._rec = sherpa_onnx.OfflineRecognizer.from_sense_voice(
            model=str(model),
            tokens=str(tokens),
            num_threads=int(self.cfg.get("voice.asr.threads", 4) or 4),
            use_itn=bool(self.cfg.get("voice.asr.use_itn", True)),
            language=str(self.cfg.get("voice.asr.language", "zh") or "zh"),
            debug=False,
        )
        self._loaded_key = key
        return self._rec

    def transcribe_file(self, path: str | Path) -> dict:
        """识别 16k 单声道 wav；返回 {text, duration}。"""
        path = Path(path)
        with wave.open(str(path), "rb") as w:
            if w.getnchannels() != 1 or w.getsampwidth() != 2:
                raise ValueError("只支持 16bit 单声道 wav")
            sr = w.getframerate()
            frames = w.getnframes()
            raw = w.readframes(frames)
        try:
            import numpy as np
        except Exception as e:  # noqa: BLE001
            raise RuntimeError("缺少 numpy，无法做语音识别") from e
        samples = np.frombuffer(raw, dtype=np.int16).astype("float32") / 32768.0
        with self._lock:
            rec = self._recognizer()
            stream = rec.create_stream()
            stream.accept_waveform(int(sr), samples)
            rec.decode_stream(stream)
            text = str(stream.result.text or "").strip()
        text = _TAG_RE.sub("", text).strip()
        self.last_error = ""
        return {"text": text, "duration": round(frames / float(sr or 16000), 2)}

    # ------------------------------------------------------------------ 视频
    def _ffmpeg(self, client) -> str:
        configured = str(self.cfg.get("voice.ffmpeg") or "").strip()
        if configured:
            p = Path(configured)
            if not p.is_absolute():
                p = ROOT / p
            if p.is_file():
                return str(p)
        try:
            from app.bili import video as video_mod

            return video_mod._find_ffmpeg(client)  # noqa: SLF001
        except Exception:  # noqa: BLE001
            return shutil.which("ffmpeg") or "ffmpeg"

    def transcribe_video(self, client, meta: dict, max_seconds: Optional[int] = None) -> dict:
        """抓视频前 N 秒音频并转写。优先 DASH 音轨，失败回退 durl。"""
        from app.bili import video as video_mod
        from app.bili.errors import BiliError

        bvid = str(meta.get("bvid") or "")
        cid = int(meta.get("cid") or 0)
        seconds = int(max_seconds or self.cfg.get("voice.asr.max_seconds", 120) or 120)
        urls: list[str] = []
        try:
            info = video_mod.playurl_audio(client, bvid, cid, meta=meta)
            urls = list(info.get("urls") or [])
        except Exception as e:  # noqa: BLE001
            self.last_error = f"音轨获取失败：{e}"
            try:
                info = video_mod.playurl(client, bvid, cid, qn=32)
                urls = list(info.get("urls") or [])
            except Exception as e2:  # noqa: BLE001
                raise BiliError(f"取播放地址失败：{e2}") from e2
        if not urls:
            raise BiliError("没有可用的音频直链")
        ffmpeg = self._ffmpeg(client)
        voice_dir = self.cfg.voice_dir
        voice_dir.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix="asr_", suffix=".wav", dir=str(voice_dir))
        os.close(fd)
        tmp = Path(tmp_name)
        ua = str(client.cfg.get("user_agent") or "")
        headers = "Referer: https://www.bilibili.com/\r\nOrigin: https://www.bilibili.com\r\n"
        if ua:
            headers += f"User-Agent: {ua}\r\n"
        cookies = "; ".join(
            f"{k}={v}" for k, v in (client.cookie_pairs or {}).items() if v
        )
        if cookies:
            headers += f"Cookie: {cookies}\r\n"
        try:
            last_err = ""
            for url in urls[:3]:
                cmd = [
                    ffmpeg, "-y", "-loglevel", "error", "-headers", headers,
                    "-t", str(max(10, seconds)), "-i", url,
                    "-vn", "-ac", "1", "-ar", "16000", "-f", "wav", str(tmp),
                ]
                try:
                    proc = subprocess.run(cmd, capture_output=True, timeout=max(60, seconds * 2 + 60))
                    if proc.returncode == 0 and tmp.is_file() and tmp.stat().st_size >= 1024:
                        out = self.transcribe_file(tmp)
                        out["url"] = url
                        out["seconds"] = seconds
                        return out
                    last_err = (proc.stderr or b"").decode("utf-8", "replace")[-300:]
                except Exception as e:  # noqa: BLE001
                    last_err = str(e)
            raise BiliError(f"ffmpeg 抽取音频失败：{last_err or '无输出'}")
        finally:
            tmp.unlink(missing_ok=True)
