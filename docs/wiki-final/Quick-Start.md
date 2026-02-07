# 快速开始

- 适用版本：`v2.0.6`
- 最后更新：`2026-02-07`

本页目标：用最短步骤完成“核心功能可用性”验证。

## 1. 运行前准备

建议确认以下条件：

- 系统：Windows 10/11
- 本机可调用 `codex` 命令（建议已加入 `PATH`）
- （可选）已安装 VS Code + ChatGPT/Codex 插件
- （可选）已安装 opencode（若需使用 opencode 配置页）

## 2. 启动程序

1. 运行 `CodexSwitcher_vX.Y.Z.exe`
2. 若出现 SmartScreen，请先核对发布来源与 SHA256
3. 打开后先进入「Codex CLI状态」页确认本机 codex 是否可识别

## 3. 首次推荐流程（5 步）

### 步骤 A：配置账号并应用

在「多账号切换」页面填写：

- 名称
- Base URL
- API Key
- Team 账号额外填写 Org ID

保存后将自动选中新账号，并触发应用链路：

- 更新 `~/.codex/config.toml` 的 `base_url`
- 更新 `~/.codex/auth.json` 的 `OPENAI_API_KEY`
- Team 场景写入 `OPENAI_ORG_ID`，非 Team 场景清理该字段

### 步骤 B：检查 Codex CLI 状态

在「Codex CLI状态」页面执行：

- 刷新本机 codex 路径/版本
- 对比官方最新版本
- 需要时执行一键更新

### 步骤 C：启动 VSCode Codex

在「VSCode Codex」页面执行：

- 选择工作区
- 点击「一键启动 VS Code」
- 如遇 WebView 异常，点击「WebView错误修改」

### 步骤 D：执行“一键增加模型”

在「VSCode Codex」页面执行：

1. 点击「扫描插件」
2. 选择目标 `index-*.js`
3. 输入模型名（支持逗号分隔）
4. 点击「备份并增加模型」

该流程采用“先备份，再写入，再提示结果”的策略。

### 步骤 E：跑一次接口诊断

在「中转站接口」页面执行诊断，确认：

- 链路连通正常
- 关键端点可用
- 目标模型可用性符合预期

## 4. 发布前最小验收标准

建议至少满足以下条件：

- 账号保存后可自动应用，配置文件写入正确
- Codex CLI 状态页可稳定识别本机版本
- VS Code 可拉起，WebView 修复动作可执行
- 增模后提示明确（成功或部分规则未更新）
- 接口诊断结果可复现且可解释
