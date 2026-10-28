# 安全与隐私说明

## 1. 不要提交的东西

以下内容只属于你的本机，**不要提交到 Git、不要发给别人、不要放进 release**：

- `data/bili_cookie.json`：B 站登录态（SESSDATA、bili_jct、DedeUserID 等）
- `config.json`：你的主人 UID、白名单、模型路径等个人配置
- `data/bot.sqlite3`：动作、评论、私信记录
- `data/logs/`、`data/cache/`、`data/voice/`
- `data/train/`：训练数据与 adapter
- `models/`、`runtime/`、`dist/`、`tools/`
- 任何 `.zip`、`.gguf`、`.onnx`、`.safetensors`、`.parts`

仓库的 `.gitignore` 已默认排除。发布前请确认：

```bash
git status --short
git ls-files | findstr /I "cookie config.json data/ dist/ models/ runtime/"
```

如果命中，先 `git rm --cached` 并清理历史（`git filter-repo` / BFG），否则即使删除当前文件，历史里仍能恢复。

## 2. 发布前自检

可以用项目自带脚本（或等价命令）扫描：

```bash
py -3 scripts\check_secrets.py
```

重点检查：

- `SESSDATA`、`bili_jct`、`DedeUserID`
- 真实 UID（如 3546...）、昵称、白名单
- `config.json` 是否被误加入
- `dist/BiliBot/data` 是否为空
- `models/` 与 `runtime/` 是否在 Git 里

## 3. Cookie 的存储边界

- 打包版与源码版都用 Windows DPAPI 加密 `data/bili_cookie.json`。
- DPAPI 绑定当前 Windows 用户；换用户/换机器通常无法解密，这不是可移植备份。
- 控制台只绑 `127.0.0.1`，没有远程登录；不要用端口映射/内网穿透暴露。
- 旧项目的 Cookie 只复制到本实例，原文件只读；不要把旧项目的 Cookie 文件一起打包。

## 4. 运行风险

- 自动评论、主动私信不是 B 站公开 API 的正常用法，可能触发限流、阿瓦隆（仅自己可见）、封号。
- 建议只用小号；首周把 `comment_per_day` 降到 2–3、`dm.proactive` 设为 `off`，先观察。
- 评论可见性受账号信用、内容、频率、请求头共同影响；`state=17` 只能检测，不能保证解除。
- 私信比评论风险更高；不要把白名单当成“安全名单”。

## 5. 大模型与提示注入

- 视频标题、简介、字幕、弹幕、评论、私信都不可信；可能包含试图操纵模型的文本。
- 生成结果经过长度、禁词、链接、AI 自曝、重复度检查；但 4B 仍可能被带偏。
- 不要把模型输出直接当命令执行；本项目不会执行模型给出的代码/URL。

## 6. 报告问题

如果发现凭据泄露、远程代码执行、控制台未授权访问等问题，请先私下联系维护者，不要直接开 public issue 贴出 Cookie 或可复现的敏感数据。
