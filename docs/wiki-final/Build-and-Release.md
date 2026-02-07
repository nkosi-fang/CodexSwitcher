# 构建与发布

- 适用版本：`v2.0.6`
- 最后更新：`2026-02-07`

本页用于规范构建与发版流程，保证结果可复现、可回溯。

## 1. 标准构建入口

执行脚本：`build.ps1`

构建步骤：

1. 安装/升级依赖（PyInstaller、PySide6、qt-material、requests、pillow）
2. 由 `icon_app.png` 生成 `icon_app.ico`
3. 使用 `codex_switcher.spec` 打包生成 exe

## 2. 版本与产物命名规则

`codex_switcher.spec` 会从 `pyside_switcher.py` 读取 `APP_VERSION`，最终产物命名为：

- `CodexSwitcher_v<APP_VERSION>.exe`

该机制保证“代码版本号”和“产物文件名”一致。

## 3. 发布前检查清单

建议按以下顺序执行：

1. 更新 `APP_VERSION`
2. 执行核心回归：
   - 账号应用链路
   - VS Code 启动与 WebView 修复
   - 一键增加模型（含备份与恢复）
   - 会话继续（CLI / VS Code）
   - 中转站接口诊断
3. 执行构建，验证产物可启动
4. 生成 SHA256 / MD5
5. 更新 `RELEASE_TEMPLATE.md`

## 4. 校验码命令

```powershell
Get-FileHash .\dist\CodexSwitcher_v2.0.6.exe -Algorithm SHA256
Get-FileHash .\dist\CodexSwitcher_v2.0.6.exe -Algorithm MD5
```

## 5. 发布说明建议模板

建议按“用户视角”组织：

- 本次新增/变化了什么能力
- 修复了什么可感知问题
- 升级后需要执行什么动作（如重启 VS Code、重扫插件）

## 6. 回滚建议

若发布后出现异常：

- 优先回滚到上一个可用 exe
- 对插件变更使用 backup 中 `.bak` 进行恢复
- 结合 `~/.codex/codex_switcher.log` 做问题复盘
