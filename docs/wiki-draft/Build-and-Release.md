# 构建与发布

本页用于规范发布流程，降低“能跑但不可复现”的风险。

## 1. 构建入口

项目使用 `build.ps1` 作为标准构建脚本，流程固定为：

1. 安装/升级依赖（`PyInstaller`、`PySide6`、`qt-material`、`requests`、`pillow`）
2. 由 `icon_app.png` 生成 `icon_app.ico`
3. 使用 `codex_switcher.spec` 执行打包

## 2. 版本号与产物命名

`codex_switcher.spec` 会从 `pyside_switcher.py` 读取 `APP_VERSION`，生成：

- `CodexSwitcher_v<APP_VERSION>.exe`

这保证了“代码版本号”和“发布文件名”一致。

## 3. 发布前技术检查清单

建议按顺序执行：

1. 更新 `APP_VERSION`
2. 核心回归：
   - 账号保存与应用
   - VSCode 启动与 WebView 修复
   - 一键增加模型（含备份与恢复）
   - 会话继续（CLI / VSCode）
   - 中转站接口诊断
3. 执行构建脚本，确认产物可启动
4. 生成校验码并记录到发布说明
5. 更新 `RELEASE_TEMPLATE.md`

## 4. 校验码命令

```powershell
Get-FileHash .\dist\CodexSwitcher_v2.0.6.exe -Algorithm SHA256
Get-FileHash .\dist\CodexSwitcher_v2.0.6.exe -Algorithm MD5
```

## 5. 发布文案建议

建议使用“用户可感知结果”表达，不建议只写内部实现细节。推荐结构：

- 功能变化：用户现在可以做什么
- 修复变化：用户遇到的问题如何被解决
- 升级提示：是否需要重启 VS Code / 重扫插件
