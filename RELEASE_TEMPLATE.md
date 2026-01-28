# Release 模板

## 标题
v2.0.2 (Windows)

## 平台
- Windows 10/11 x64

## 下载
- CodexSwitcher_v2.0.2.exe

## 校验（可选）
- SHA256: 13c610b0c978da0cbce2447b43000f731df44a42653ef3a3c6220db37d5cd2fb

## 首次运行提示
- 如首次运行出现 Windows SmartScreen 提示，这是因为未进行代码签名；请确认下载来源为 GitHub Releases，并核对 SHA256 后再运行。

## 变更
- 修复：接口诊断请求补充 UA，降低 WAF/中转站 403(1010) 拦截。
- 更新：账号池可用模型探测提示文案，提醒查看日志。

## 已知问题

- codex cli最新版本0.92可能会出现沙盒相关警告，请自行手动在config.toml中添加“suppress_unstable_features_warning = true”。添加位置如截图：

