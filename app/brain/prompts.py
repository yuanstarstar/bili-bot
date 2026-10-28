# -*- coding: utf-8 -*-
"""提示词模板：钩子、首评、自评、楼中楼、私信、选视频。"""
from __future__ import annotations

from app.brain.persona import length_range


def hooks(ctx_text: str, n: int = 3) -> str:
    return f"""下面是视频信息。请找出 {n} 个普通观众会想评论的点：可以是槽点、梗、信息点、情绪点。
不要总结视频，不要写“这个视频讲述了”。
只输出 JSON：{{"hooks":["...","...","..."]}}

视频信息：
{ctx_text}"""


def comment(ctx_text: str, persona: dict, hook_list: list[str], examples: list[str], n: int = 3) -> str:
    lo, hi = length_range(persona, "comment")
    hooks_text = "、".join(hook_list) if hook_list else "（自己找一个具体的点）"
    ex = "\n".join(f"- {x}" for x in examples[:10]) if examples else "（无）"
    return f"""你要以 B 站用户身份，在这条视频下发一条首评。
视频信息：
{ctx_text}

可评论的点：{hooks_text}

同分区高赞评论风格参考（只学语感，绝不照抄）：
{ex}

要求：
- 写 {n} 条不同角度、不同句式的评论；
- 每条 {lo}-{hi} 个汉字，口语、短句、具体；
- 可以吐槽、接梗、提问、共鸣，不要空泛夸；
- 不要列点、不要总结、不要提 AI；
- 不要重复参考里的句子。
只输出 JSON：{{"comments":["...","...","..."]}}"""


def critique(ctx_text: str, candidates: list[str]) -> str:
    lines = "\n".join(f"{i}. {c}" for i, c in enumerate(candidates))
    return f"""下面是 3 条准备发在 B 站视频下的评论。请判断哪一条最像真人、最具体、最不冒犯。
视频信息（节选）：{ctx_text[:800]}
评论：
{lines}
评分维度：具体性、口语度、像真人程度（越低 AI 味越好）、冒犯风险（越低越好）。
只输出 JSON：{{"best":0,"scores":[4,3,5],"safe":true}}"""


def reply(ctx_text: str, persona: dict, parent_text: str, n: int = 3,
          thread_text: str = "", parent_uname: str = "") -> str:
    lo, hi = length_range(persona, "nested")
    who = parent_uname or "对方"
    history = thread_text.strip() or "（这层楼还没有更早的对话）"
    return f"""有人在你的 B 站评论下回复了你。你要在楼中楼回他。
视频信息（尽量结合原视频内容，不要只看这一句）：
{ctx_text[:1800]}

这层楼之前的对话（按时间从早到晚）：
{history}

现在「{who}」回复你：{parent_text}

要求：
- 写 {n} 条回复，每条 {lo}-{hi} 个汉字；
- 必须结合原视频和上面更早的对话来回应，不要像没看过视频、没看过前文；
- 回复里至少承接一个前文信息点（对方之前说过的话，或原视频里的具体点），不要只对最后一句话做条件反射；
- 顺着对方的话接，可以反问、吐槽、认同、玩梗；
- 不要重新介绍视频，不要像客服，不要解释，不要提 AI。
只输出 JSON：{{"replies":["...","...","..."]}}"""


def dm(persona: dict, uname: str, history: list[dict], latest: str) -> str:
    lo, hi = length_range(persona, "dm")
    hist = "\n".join(f"{'我' if m.get('role') == 'assistant' else uname}：{m.get('content')}"
                     for m in history[-20:]) or "（还没有聊天记录）"
    return f"""你在 B 站私信里和网友「{uname}」聊天。
更早的聊天记录（按时间从早到晚）：
{hist}
对方最新：{latest}

写一条回复：2-60 个汉字，像微信聊天，自然、不客服、不解释。
要求：
- 结合上面更早的聊天内容，不要假装刚认识，不要重复问过的问题；
- 回复里尽量接住对方之前提过的一个具体信息，不要只对最后一句话做条件反射；
- 不要威胁、辱骂、贬低对方；对方挑衅时冷处理或幽默带过，不跟着升级冲突；
- 不要复读对方刚说的话，也不要把自己上一句原样再说一遍；
- 对方只发“在吗”这类短句时，回“在的，刚在忙”这种自然短句，不要只回一个字。
只输出 JSON：{{"reply":"..."}}"""


def score_video(persona: dict, video: dict) -> str:
    interests = "、".join(str(x) for x in persona.get("interests") or [])
    return f"""你是 B 站用户，兴趣是：{interests}。
根据下面的视频信息，判断你有多想看、多想评论（0-10 分）。
标题：{video.get('title', '')}
UP 主：{(video.get('owner') or {}).get('name', '')}
时长：{int(video.get('duration') or 0)} 秒
播放：{(video.get('stat') or {}).get('view', 0)}
标签/分区：{video.get('tname', '')}
只输出 JSON：{{"score":8,"why":"一句话理由"}}"""
