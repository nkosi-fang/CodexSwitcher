# 数据文件与路径

- 适用版本：`v2.0.6`
- 最后更新：`2026-02-07`

本页用于说明程序会读取/写入哪些路径，帮助你做权限评估与故障定位。

## 1. 核心目录：`~/.codex`

- `codex_profiles.json`：账号池与激活账号
- `config.toml`：provider 配置（含 `base_url`）
- `auth.json`：认证信息（API Key、Org ID）
- `codex_switcher.log`：异常与诊断日志
- `sessions/`：会话原始数据（jsonl）
- `history.jsonl`：会话检索索引源

## 2. VS Code 插件扫描路径

程序会在以下位置查找扩展：

- `~/.vscode/extensions`
- `~/.vscode-insiders/extensions`
- `~/.vscode-oss/extensions`
- `~/.cursor/extensions`
- `VSCODE_EXTENSIONS`（若环境变量存在）

## 3. VS Code 设置写入路径

关闭扩展自动更新时，可能修改：

- `%APPDATA%/Code/User/settings.json`
- `%APPDATA%/Code - Insiders/User/settings.json`
- `%APPDATA%/VSCodium/User/settings.json`
- `%APPDATA%/Cursor/User/settings.json`

## 4. 模型补丁备份路径

当你修改插件 `index-*.js` 时，会在同级创建：

- `backup/` 目录
- 带时间戳的 `.bak` 文件

该设计用于快速回退与比对差异。

## 5. opencode 配置路径

- `~/.config/opencode/opencode.json`

该文件可由账号池一键映射生成，也可手工编辑后保存。

## 6. Skill 路径

- 根目录：`~/.codex/skills`
- 系统技能：`~/.codex/skills/.system`
- 用户技能：`~/.codex/skills/user`
- 备份目录：`~/.codex/skills_backup_YYYYmmdd_HHMMSS`

## 7. 权限与安全建议

如遇写入失败，优先检查：

- 路径写权限
- 文件是否被占用
- Windows 下只读/隐藏属性
