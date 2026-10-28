# 发布到 GitHub

当前目录已经是一个本地 Git 仓库（用 dulwich 初始化，不依赖本机 git 命令）。仓库只包含源代码、文档、测试和小体积脚本；不包含模型、运行时、Cookie、数据库、`dist/`、`models/`。

## 1. 先自检

```bat
py -3 scripts\check_secrets.py
py -3 -m unittest discover -s tests -q
```

确认输出没有 Cookie、真实 UID、API key。

## 2. 在 GitHub 新建仓库

建议仓库名：`bili-bot`。**不要**勾选自动生成 README/LICENSE（本地已有）。

## 3. 推送

如果本机装了 Git：

```bat
cd /d D:\bilibili\bili-bot
git remote add origin https://github.com/<你的账号>/bili-bot.git
git branch -M main
git push -u origin main
```

如果本机没有 Git，可以用 GitHub Desktop：

1. Add Local Repository → 选择 `D:\bilibili\bili-bot`
2. 确认 Changes 里没有 `data/`、`config.json`、`dist/`、`models/`、`runtime/`
3. Publish repository（不要勾 Private 之外的多余选项）

## 4. Release 包

源码仓库不要提交 6.5GB 的 zip。到 GitHub Releases 上传：

- `dist/BiliBot-0.1.0-win64.zip`
- 在 release notes 里写 SHA256、包含的模型、ASR 模型、许可证

> **注意**：GitHub Releases 单个 asset 通常有 2GB 上限（免费账户），本包约 6.5GB，直接上传会失败。推荐：
> 1. 用 7-Zip 分卷压缩，每卷 < 1.9GB，再逐个上传；或
> 2. 把 zip 放网盘/对象存储，在 Release notes 里给链接 + SHA256。
> 源码和文档仍然正常 push 到仓库。

## 5. 注意

- 仓库已经 `.gitignore` 掉 `data/`、`config.json`、`dist/`、`models/`、`runtime/`、`tools/`、`*.zip`、`*.gguf`、`_*.py`。
- 如果你之前手动 `git add -f` 过敏感文件，需要清理 Git 历史（`git filter-repo`/BFG），仅删除当前文件不够。
- `config.example.json` 可以公开；`config.json` 不要。
- `personas/default.json` 是示例人设，不含你的账号信息。
