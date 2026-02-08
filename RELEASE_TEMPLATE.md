# Release 模板

## 标题
v2.0.8 (Windows)

## 下载
- CodexSwitcher_v2.0.8.exe

## 校验（可选）
SHA256: 246e57b6a2dec823ed8d630c4e4622b2890db1aecdef6d62e15305931603f463
MD5:    c63da1f61e5e9839c5017037faa01311

## 首次运行提示
- 如首次运行出现 Windows SmartScreen 提示，这是因为未进行代码签名；请确认下载来源为 GitHub Releases，并核对 SHA256 后再运行。

## 变更
- 修复部分用户反馈的“一键增加vscode codex模型”功能在Windows环境中出现：“模型已增加，但部分规则未更新：apikey-order、initial-data。
可重启VS Code后验证模型下拉；若仍可修改用手动索引文件”的已知错误提示。
- 版本号升级为 v2.0.8，并完成 Windows 可执行文件重新打包。
