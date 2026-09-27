# YPK 制作工坊 0.1.0

给 Codex 提供新卡网页、卡图或卡文，制作含中文卡名/卡效、Lua 和卡图的 YGOPro YPK，或向指定旧 YPK 追加卡片。包内有一个可复用技能和一个命令行工具。识别、翻译、效果编程由 Codex 模型完成，工具负责图片处理、数据库及封包。

两个使用场景：

- “用 `$ypk-workshop` 把这个官方网页中的新卡制作成中文 YPK，含卡图，目标客户端在我指定的目录。”
- “用 `$ypk-workshop` 把图片中的卡片加入这个已有 YPK，输出新版本，并保留原包。”

初版面向 YGOPro/Fluorohydride 家族，其他模拟器需兼容适配。需要 Python 3.10+、Pillow，以及能够浏览、看图、执行文件工具的 Codex。无需为本包单独配置模型 API。

## 使用技能

解压后，把 `skills/ypk-workshop` 整个目录复制到项目 `.agents/skills/ypk-workshop`，从该项目使用 Codex，再用 `$ypk-workshop` 提供请求和输入。也可按自己的技能管理方式装到用户技能目录。发现规则见 [OpenAI 官方说明](https://learn.chatgpt.com/docs/build-skills)。

包内包含 `.codex-plugin/plugin.json`，可用于插件分发，也可直接作为本地技能使用。脚本按安装位置解析，工程和客户端路径由使用者提供。

## 单独使用封包工具

在解压目录创建虚拟环境，例如 Windows PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r .\skills\ypk-workshop\requirements.txt
.\.venv\Scripts\python.exe .\skills\ypk-workshop\scripts\ypk.py --help
```

macOS/Linux 使用 `.venv/bin/python`。命令行本身不接受一个链接就生成效果代码，需要 Codex 技能或使用者先准备工程。

```powershell
python .\skills\ypk-workshop\scripts\ypk.py init --project .\card-project --name my-new-cards
python .\skills\ypk-workshop\scripts\ypk.py inspect .\old.ypk
python .\skills\ypk-workshop\scripts\ypk.py build --project .\card-project --base .\old.ypk --output .\expanded.ypk --engine-root CLIENT_DIR
python .\skills\ypk-workshop\scripts\ypk.py validate .\expanded.ypk
```

`CLIENT_DIR` 换成实际客户端目录。先由技能填 `project.json`、Lua、原图，再 `build`。新建时省略 `--base`；虚拟环境中将 `python` 换成对应路径。完整格式见 [工具说明](skills/ypk-workshop/references/tool-contract.md)。

## 验证范围

工具检查 CRC、数据库、ID/资源、哈希，并可扫描客户端卡号冲突。追加保留原成员内容，写出新文件；同 ID、重复资源、未决卡文或已有输出会报错。

Lua 语法需提供目标版本 `luac`。封包成功不证明效果正确，技能会附实测步骤，报告已执行的验证。未实测明确标注；卡文读不清需补充来源。

本版追加新卡，不替换旧卡。旧卡修复、正式编号迁移及其他客户端适配需后续实现。

## 测试

```text
python -m unittest discover -s tests -v
```
