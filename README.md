# Codex 游戏王 YPK 技能工具

作者：沐音 · 版本：0.1.0

供 Codex 使用的游戏王 YPK 制作技能与命令行工具，支持从新卡网页或卡图准备卡片，再新建扩展包或向已有 YPK 追加卡片。

## 下载

[下载 v0.1.0 工具包](./Codex游戏王YPK技能工具_v0.1.0_by沐音.zip)

## 功能

- Codex 技能：读取网页或图片、核对原文、翻译卡名和效果、编写目标引擎 Lua。
- 命令行工具：写入 CDB、裁切和转换卡图、新建或追加 YPK、检查卡号冲突与封包完整性。
- 追加输出新文件，保留原包。当前版本不替换同 ID 的已有卡片。

## 使用

需要 Codex、Python 3.10+ 和 Pillow。当前适配 YGOPro/Fluorohydride 家族。

1. 下载并解压工具包，或使用本仓库中的 `ypk-workshop` 目录。
2. 将其中的 `skills/ypk-workshop` 复制到你的项目 `.agents/skills/ypk-workshop`。
3. 在 Codex 中使用 `$ypk-workshop`，提供当前任务的网页、图片、卡文和目标客户端；追加时提供目标 YPK。
4. 按技能提示安装依赖、生成卡片工程并封包。

[详细使用说明](ypk-workshop/README.md) · [技能入口](ypk-workshop/skills/ypk-workshop/SKILL.md) · [命令行与数据格式](ypk-workshop/skills/ypk-workshop/references/tool-contract.md)

识别、翻译和效果编程由 Codex 完成。命令行工具负责确定性处理，不包含独立模型 API。封包校验和 Lua 语法检查不能证明效果正确，实际卡效需要在目标客户端中测试。
