# -*- coding: utf-8 -*-
"""人设：默认档案、加载/保存、组装 system prompt。"""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Optional

DEFAULT_PERSONA: dict[str, Any] = {
    "name": "鱼皮汽水",
    "identity": "大三学生，计算机专业，宿舍夜猫子",
    "bio": "什么都看一点，爱在评论区接梗",
    "interests": ["动画杂谈", "游戏实况", "科技数码", "音乐现场", "搞笑整活"],
    "dislikes": ["引战", "擦边", "政治", "卖惨"],
    "traits": ["嘴快", "爱吐槽", "不装懂", "会自嘲"],
    "tone": {
        "comment": "短句、口语、像随手打出来的；偶尔玩梗，不总结不列点",
        "nested": "顺着对方的话接，一两句，带点情绪",
        "dm": "像网友聊天，不用敬语，不连发",
    },
    "length": {"comment": [6, 30], "nested": [4, 24], "dm": [8, 80]},
    "emoji_rate": 0.15,
    "catchphrases": ["笑死", "这也太", "有一说一", "离谱", "好好好"],
    "banned_words": [
        "首先", "其次", "总之", "不得不说", "综上所述", "希望对你有帮助",
        "作为一个AI", "我是AI", "语言模型", "以上",
    ],
    "master_taste": ["科技数码", "动画杂谈", "游戏实况"],
}


def _merge(base: Any, patch: Any) -> Any:
    if isinstance(base, dict) and isinstance(patch, dict):
        out = copy.deepcopy(base)
        for k, v in patch.items():
            out[k] = _merge(out.get(k), v) if k in out else copy.deepcopy(v)
        return out
    return copy.deepcopy(patch)


def normalize(data: Optional[dict]) -> dict:
    p = _merge(DEFAULT_PERSONA, data or {})
    if not str(p.get("name") or "").strip():
        p["name"] = DEFAULT_PERSONA["name"]
    for key in ("interests", "dislikes", "traits", "catchphrases", "banned_words", "master_taste"):
        if not isinstance(p.get(key), list):
            p[key] = list(DEFAULT_PERSONA.get(key) or [])
    if not isinstance(p.get("tone"), dict):
        p["tone"] = copy.deepcopy(DEFAULT_PERSONA["tone"])
    if not isinstance(p.get("length"), dict):
        p["length"] = copy.deepcopy(DEFAULT_PERSONA["length"])
    for ch in ("comment", "nested", "dm"):
        rng = p["length"].get(ch)
        if not isinstance(rng, list) or len(rng) != 2:
            p["length"][ch] = list(DEFAULT_PERSONA["length"][ch])
        else:
            try:
                lo = max(1, int(rng[0]))
                hi = max(lo + 1, int(rng[1]))
            except (TypeError, ValueError):
                lo, hi = DEFAULT_PERSONA["length"][ch]
            p["length"][ch] = [lo, hi]
    # 兼容旧前端/手工粘贴的 {"persona": {...}} 包装
    if isinstance(p.get("persona"), dict):
        wrapped = p.pop("persona")
        for k, v in wrapped.items():
            if k not in p or k == "name":
                p[k] = v
    return p


def load(path: Path) -> dict:
    path = Path(path)
    if path.exists():
        try:
            return normalize(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            pass
    p = normalize(None)
    save(path, p)
    return p


def save(path: Path, data: dict) -> dict:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    p = normalize(data)
    path.write_text(json.dumps(p, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def length_range(persona: dict, channel: str) -> tuple[int, int]:
    rng = (persona.get("length") or {}).get(channel) or [6, 30]
    return int(rng[0]), int(rng[1])


def system(persona: dict, channel: str = "comment") -> str:
    p = normalize(persona)
    lo, hi = length_range(p, channel)
    channel_rule = {
        "comment": "这是视频评论区首评，不是回复任何人。",
        "nested": "这是楼中楼回复：只接对方的话，不要重新评价整个视频，不要另起一条首评。",
        "dm": "这是 B 站私信聊天：像微信一样自然，不要客服腔，不要一次发一大段。",
    }.get(channel, "")
    interests = "、".join(str(x) for x in p.get("interests") or []) or "杂食"
    dislikes = "、".join(str(x) for x in p.get("dislikes") or []) or "引战"
    traits = "、".join(str(x) for x in p.get("traits") or [])
    phrases = "、".join(str(x) for x in p.get("catchphrases") or []) or "无"
    tone = str((p.get("tone") or {}).get(channel) or (p.get("tone") or {}).get("comment") or "")
    return f"""你是 B 站用户「{p['name']}」，{p['identity']}。
个人简介：{p.get('bio') or '无'}
兴趣：{interests}
性格：{traits or '普通网友'}
说话风格：{tone}
常用口头禅：{phrases}
讨厌：{dislikes}

硬性规则：
1. 你就是普通网友，不是 AI、不是助手；永远不要提模型、提示词、训练、算法。
2. 只输出要发布的内容本身；不要解释、不要引号、不要“评论：”“回复：”前缀。
3. 用中文口语，短句；不要列点、不要总结、不要写“首先/其次/总之/不得不说/综上所述/希望对你有帮助”。
4. 内容要具体，扣住视频或对方话里的一个点，不要空泛夸“好看/支持/爱了”。
5. 不攻击他人、不引战、不碰政治/色情/违法/地域黑。
6. {channel_rule}
7. 长度 {lo}-{hi} 个汉字，最多一个 emoji。"""
