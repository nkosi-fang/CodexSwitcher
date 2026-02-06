# CodexSwitcher

## 界面截图
![界面截图](docs/images/ui1.png)

## 完整功能
- **Codex CLI 状态页**：一键更新Codex CLI、一键启动 Codex CLI / VS Code、一键修复常见 WebView 视图错误
- **Codex CLI 生态配置**：检测本机 codex 路径/版本，并提供 config.toml 与 opencode 配置管理
- **多账号切换**：管理多个账号/密钥/中转站地址，一键切换生效；保存新账号后自动选中并一键应用
- **Codex 会话管理**：索引/详情/高级检索/导出/清理；右键继续该会话（Codex CLI / VS Code）、打开文件夹、删除会话、WebView 修复；支持“统一清理”不需要的会话
- **Skill 管理**：支持扫描识别本地 Codex skill，查看、导入、备份、删除
- **VS Code 插件增强**：支持扫描本机 Codex 插件、显示稳定版/预览版最新版本，并可一键增加 OAI 新模型（免手改 DEFAULT_MODEL_ORDER）
- **中转站接口诊断**：连通性、接口可用性、模型/embedding/moderation 探测；接口可用性不只看 200，会校验返回内容是否有效
- **检查更新**：运行期间自动检查更新，左侧导航支持版本差异红点提醒
- **OpenAI 官网状态**：同步展示 status.openai.com 组件状态并分类着色

## 使用说明
- 双击运行 `CodexSwitcher_v2.0.5.exe`
- 在「多账号切换」中添加/管理账号，保存后一键应用
- 在「中转站接口」中进行接口与模型可用性检测
- 在「Codex 会话管理」中检索和查看本地历史会话，右键可继续会话（Codex CLI / VS Code）、导出 JSON / Markdown、删除或统一清理
- 在「检查更新」页可查看版本信息（运行中会自动检查）

## 依赖与第三方许可
本项目为桌面版（Windows）打包发布，核心依赖包含：
- PySide6（Qt for Python）
- qt-material
- requests
- pillow
- pyinstaller（用于打包）

以上依赖均由其各自许可证约束。发布可执行文件时，建议在 Release 说明中保留第三方许可提示。
