# 数据文件与路径

本页说明程序会读取/写入哪些本地路径，便于运维排查与权限评估。

## 1. 核心目录：`~/.codex`

- `codex_profiles.json`：账号池与当前激活账号
- `config.toml`：Codex provider 配置（如 `base_url`）
- `auth.json`：认证字段（如 `OPENAI_API_KEY`、`OPENAI_ORG_ID`）
- `codex_switcher.log`：异常与诊断日志
- `sessions/`：会话原始数据（jsonl）
- `history.jsonl`：会话检索索引源

## 2. VS Code 插件扫描路径

程序会在以下常见目录查找扩展：

- `~/.vscode/extensions`
- `~/.vscode-insiders/extensions`
- `~/.vscode-oss/extensions`
- `~/.cursor/extensions`
- `VSCODE_EXTENSIONS`（若设置）

## 3. VS Code 设置文件（自动更新开关）

关闭自动更新时，可能写入：

- `%APPDATA%/Code/User/settings.json`
- `%APPDATA%/Code - Insiders/User/settings.json`
- `%APPDATA%/VSCodium/User/settings.json`
- `%APPDATA%/Cursor/User/settings.json`

## 4. VSCode 模型补丁备份

目标 `index-*.js` 同级会创建 `backup/` 目录，并生成时间戳 `.bak` 文件，用于快速回退。

## 5. opencode 路径

- `~/.config/opencode/opencode.json`

该文件可由“账号池映射”自动更新，也支持手工编辑后保存。

## 6. Skill 目录与备份

- 技能根目录：`~/.codex/skills`
- 系统技能：`~/.codex/skills/.system`
- 用户技能：`~/.codex/skills/user`
- 备份目录：`~/.codex/skills_backup_YYYYmmdd_HHMMSS`

## 7. 路径权限建议

若出现“保存失败/写入失败”，优先检查：

- 目录是否存在写权限
- 文件是否被其他进程占用
- Windows 下是否存在只读或隐藏属性限制
