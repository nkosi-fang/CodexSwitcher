# Release 模板

## 标题
v2.0.9 (Windows)

## 下载
- CodexSwitcher_v2.0.9.exe

## 校验（可选）
SHA256: a22048d42681fa2fa262b6b3a0ca6c0050178b887844f365ae80354315a7dfc4
MD5:    a3ef0353ff89fd68e9bcba251c07b585

## 首次运行提示
- 如首次运行出现 Windows SmartScreen 提示，这是因为未进行代码签名；请确认下载来源为 GitHub Releases，并核对 SHA256 后再运行。

## 变更
- 修复 VSCode Codex 插件走“动态模型流”时，“一键增加模型”后模型下拉未出现 `gpt-5.3-codex` 的问题。
- 提示：该补丁依赖官方插件当前实现细节，后续 OpenAI 插件更新可能导致失效；如遇异常请及时反馈，并关注我们的最新版本。
- 修复“检查更新”页开发者反馈二维码不显示（打包内置 `developer_qr.png`）。
- v2.0.9 重新打包发布（Windows）。
