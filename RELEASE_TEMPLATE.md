# Release 模板

## 标题
v2.0.4 (Windows)

## 下载
- CodexSwitcher_v2.0.4.exe

## 校验（可选）
- SHA256: EA329495B1B9CE6EB23510FECFFD606D40106118D9F5F44636A545F4759847ED

## 首次运行提示
- 如首次运行出现 Windows SmartScreen 提示，这是因为未进行代码签名；请确认下载来源为 GitHub Releases，并核对 SHA256 后再运行。

## 变更
1) Skill 管理功能完善

  - 识别并读取 ~/.codex/skills 与 ~/.codex/skills/user 子目录
  - 解析 SKILL.md 中的 name/description 用于展示
  - 系统技能只读保护
  - 增加“备份技能 / 打开备份目录 / 打开技能目录”
  - 备份保留策略（保留最近 5 份）

  2) Codex 状态页增强

  - 移除“诊断信息”模块显示
  - 官方版本卡片新增“一键更新”
  - 新增“一键启动”区：
      - 选择工作区
      - 一键启动 Codex CLI
      - 一键启动 VS Code
  - VS Code 启动后自动打开 Codex 侧边栏（能用 --command 时）
  - VS Code 工作区启动失败时改为写入 chatgpt.openOnStartup

  3) VS Code 安装目录管理

  - 可选择并保存 VS Code 安装目录
  - 下次启动/修复优先使用该目录
  - 便携版支持（检测 data/user-data）

  4) WebView 错误修复功能

  - 新增“WebView错误修改”按钮
  - 自动结束 VS Code / WebView 相关进程
  - 清理 WebView 与缓存目录
  - 修复后自动重启 VS Code

  5) Codex VS Code 插件配置页

  - 插件扫描、版本展示、最新版本（Marketplace）
  - index 文件路径自动换行显示
  - 提示文案优化（重启提示、原理说明等）
  - 导航名称已改为 “VScode codex”

  6) 会话管理增强

  - 右键菜单新增：
      - “VS Code打开该目录”
      - 只打开侧边栏 + 当前会话目录
      - 弹窗说明插件暂不支持通过 session_id 继续
  - 提示文案同步更新

  - 适配 .cmd/.bat 启动方式（cmd /k）
  - 支持 npm prefix -g 路径探测
  - 避免 “找不到文件” 报错
## 已知问题

- codex cli最新版本0.92可能会出现沙盒相关警告，请自行手动在config.toml中添加“suppress_unstable_features_warning = true”。添加位置如截图：

