# Release 模板

## 标题
v2.0.3 (Windows)

## 下载
- CodexSwitcher_v2.0.3.exe

## 校验（可选）
- SHA256: 16F92BEAD560AEDD7D771C27882DDD690A30A695BE1191544381B36A3783BB6E

## 首次运行提示
- 如首次运行出现 Windows SmartScreen 提示，这是因为未进行代码签名；请确认下载来源为 GitHub Releases，并核对 SHA256 后再运行。

## 变更
- 新增「Codex会话管理」页：集中管理会话索引、详情、导出（JSON/Markdown）与清理，便于查找与归档。
- 归档视图优化：在 user/assistant/系统提示/开发者说明/工具响应之间自动插入分割线，阅读更清晰。
- 会话检索增强：支持 OR/AND 多关键词、history 优先 + 深度搜索、进度与取消，可检索本地全部 Codex 会话。
- 会话列表右键：支持“继续该会话（Codex CLI）”，在 PowerShell 终端快速续聊。
- OpenAI官网状态：刷新后同步官方服务状态，或一键打开 status.openai.com。

## 已知问题

- codex cli最新版本0.92可能会出现沙盒相关警告，请自行手动在config.toml中添加“suppress_unstable_features_warning = true”。添加位置如截图：

