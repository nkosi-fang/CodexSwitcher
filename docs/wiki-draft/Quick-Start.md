# 快速开始

本页面向“首次使用者”和“发布验收者”。目标是用最短路径完成核心能力验证。

## 1. 运行前准备

建议先确认：

- 系统：Windows 10/11
- 本机可调用 `codex` 命令（建议已加入 `PATH`）
- （可选）已安装 VS Code 与 ChatGPT/Codex 插件
- （可选）已安装 opencode（若要使用 opencode 配置页）

## 2. 启动与安全校验

1. 运行 `CodexSwitcher_vX.Y.Z.exe`
2. 若首次出现 SmartScreen，先核对下载来源与 SHA256
3. 进入程序后先看「Codex CLI状态」页是否能识别本机 codex

## 3. 首次推荐流程（5 步）

### 步骤 A：配置账号并应用

在「多账号切换」页填写：

- 名称
- Base URL
- API Key
- Team 账号额外填写 Org ID

保存后会自动选中新账号，并触发“一键应用”链路：

- 更新 `~/.codex/config.toml` 的 `base_url`
- 更新 `~/.codex/auth.json` 的 `OPENAI_API_KEY`
- Team 场景写入 `OPENAI_ORG_ID`，非 Team 场景清理该字段

### 步骤 B：确认 Codex CLI 运行面

在「Codex CLI状态」页：

- 刷新本机 codex 路径与版本
- 对比最新版本
- 需要时执行一键更新

### 步骤 C：启动 VSCode Codex

在「VSCode Codex」页：

- 选择工作区
- 点击「一键启动 VS Code」
- 若出现 WebView 异常，点击「WebView错误修改」

### 步骤 D：执行“一键增加模型”

在「VSCode Codex」页：

1. 点击「扫描插件」
2. 选择目标 `index-*.js`
3. 输入模型名（支持逗号分隔）
4. 点击「备份并增加模型」

该流程是“先备份，再写入，再提示结果”。

### 步骤 E：做一次中转站接口诊断

在「中转站接口」页执行检测，确认：

- 链路连通性正常
- 关键端点可用
- 目标模型可用性符合预期

## 4. 建议验收标准（发版前）

满足以下条件，基本可判定核心链路可用：

- 账号保存后可自动应用，配置文件写入正确
- Codex CLI 状态页能稳定识别本机版本
- VS Code 可正常拉起，WebView 修复可执行
- 增模后出现“模型已增加/规则已更新”或明确的部分失败提示
- 接口诊断结果可复现，且非“仅 200 成功”逻辑
