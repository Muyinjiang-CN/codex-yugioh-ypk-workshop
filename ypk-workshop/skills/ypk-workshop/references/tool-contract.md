# 工具与数据格式 0.1.0

Python 3.10+，图像处理需 Pillow，依赖在技能根目录 `requirements.txt`。识别、翻译、Lua 编写由 Codex 完成；命令行不调用模型 API。命令输出 JSON，成功退出 0，预期错误退出 2。不覆盖源包或已有输出，`build --base` 只追加新 ID。

```text
python scripts/ypk.py init --project card-project --name my-new-cards --source-url https://example.com/source
python scripts/ypk.py inspect old.ypk
python scripts/ypk.py ids --engine-root CLIENT_DIR --base old.ypk --start 260000000 --count 5
python scripts/ypk.py build --project card-project --output new.ypk --engine-root CLIENT_DIR
python scripts/ypk.py build --project card-project --base old.ypk --output expanded.ypk --engine-root CLIENT_DIR
python scripts/ypk.py validate expanded.ypk --luac LUAC_PATH
python scripts/ypk.py image source.webp --output card.jpg
python scripts/ypk.py image poster.jpg --crop 60 50 480 685 --output card.jpg
```

示例坐标仅演示接口，每图按实际边界指定。默认保留裁切后尺寸，`--max-edge 1024` 可等比例缩小，不消除 `SAMPLE` 或重绘文字。`ids` 只检查给定客户端/包中的占用，不确定正式卡密，也不保证他人的包不冲突。

## 卡片工程

工程保存 `project.json`、`script/cID.lua`、`images/原图` 和由 agent 编写的 `test-plan.md`。示例文本必须替换为实际来源，Lua 需实现真实效果：

```json
{
  "schema_version": 1,
  "package": {
    "name": "my-new-cards",
    "display_name": "我的新卡包",
    "author": "用户指定作者",
    "homepage": "https://example.com/source"
  },
  "target": {
    "family": "ygopro-fluorohydride",
    "script_api": "记录实际确认的客户端版本及辅助函数"
  },
  "cards": [{
    "id": 260000000,
    "name": "中文卡名或允许保留的原名",
    "desc": "完整中文卡效",
    "ot": 1,
    "alias": 0,
    "setcode": 0,
    "type": "0x21",
    "atk": 1600,
    "def": 1200,
    "level": 4,
    "race": "0x1",
    "attribute": "0x1",
    "category": 0,
    "strings": ["对应 aux.Stringid(id,0) 的提示"],
    "script": "script/c260000000.lua",
    "image": {"path": "images/source.webp"},
    "source": {
      "url": "https://example.com/source",
      "set_number": "实际发行编号",
      "original_name": "卡名原文",
      "original_text": "来源中的完整原文卡效",
      "language": "ja",
      "temporary_id": true,
      "image_origin": "原图来源或用户提供图片的说明"
    },
    "effect_notes": [],
    "unresolved": []
  }]
}
```

这些合成数据仅演示字段格式，不是任何实际卡片或所有卡的默认类型。真实值取目标常量。裁切时 `image` 加 `crop: [left, top, right, bottom]`，可加 `max_edge`。

`id,type,atk,def,level,race,attribute` 必填；`ot,alias,setcode,category` 默认 1、0、0、0，真实卡应明确正确值。数字可为十进制整数或 `0x...` 字符串。`setcode` 是组合字段，灵摆 `level` 包含刻度位，连接怪兽 `def` 可编码箭头，不能直接代入普通等级/守备力。

原文必填，`unresolved` 显式为 `[]` 才可封包。`strings` 最多16条，`aux.Stringid(id,0)` 对应 `str1`。来源、原文和备注进入包内清单；不要把无关个人信息放进工程。临时 ID 换正式编号需同步 CDB、Lua、自定义引用和图片名。

## 包与追加

新包包含根目录 `ypkw-PACK_NAME-CONTENT_TOKEN.cdb`、`script/cID.lua`、`pics/ID.jpg`、`corres_srv.ini`、`ypk-workshop/manifest.json`。适配以 ZIP 读取 YPK 的 YGOPro 家族。

追加独立 CDB，只存新卡，保留原库及额外字段。多个 CDB 中重复 ID 时拒绝。CDB 名含包名与内容摘要，降低上游 [同名 CDB 误读问题](https://github.com/Fluorohydride/ygopro/issues/3066) 的风险。实测时启用一个包版本，不能同时装新旧版本造成重复 ID。

保留未知成员、旧 `manifest.json`、README、牌组、ZIP 注释，新增信息在工坊清单。继续追加时只更新工坊清单，其他旧成员内容核对不变。旧 `corres_srv.ini` 仍可能写原始包名/文件名，当前输出名以工坊清单为准；客户端特定服务器发布元数据调整不属于本命令。

拒绝重复路径（忽略大小写）、路径穿越、符号链接、加密、不支持的 ZIP 压缩、超过512MiB总解压大小或256MiB单成员。数据库验证用独立临时副本，未知/损坏客户端库不会被忽略后宣称无冲突。

## 检查结论

报告分别列 `package_structure`、`lua_syntax`、`engine_collisions`、`effect_runtime`。没有 `--engine-root` 时 ID 检查 `not_checked`，没有 `--luac` 时语法 `not_checked`，实际对局始终 `not_verified`，须附引擎测试证据。

追加扫描针对新增 ID，原包已存在的客户端卡不属于此结论。工具不联网确认卡文/翻译。本版不含独立模型 API、GUI、已有卡替换、自动正式编号迁移或客户端自动安装；网页和图片完整流程由 Codex 技能处理。
