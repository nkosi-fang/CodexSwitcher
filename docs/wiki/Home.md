# CodexSwitcher Wiki（正式版）

- 适用版本：`v2.0.6`
- 最后更新：`2026-02-07`
- 维护状态：持续维护

## 项目简介

CodexSwitcher 是一个面向 Windows 的桌面管理工具，核心目标是把 Codex CLI、VSCode Codex 插件、账号配置、会话管理与诊断能力整合到同一入口。

相较于“手动改文件 + 手动排障”，本项目通过可视化流程把高频且易错的操作标准化，重点覆盖：

- 账号切换与配置落盘
- VSCode 插件模型规则更新（含备份/恢复）
- 会话检索、导出、清理、继续会话
- 中转站接口与模型可用性诊断
- 版本检查与发布节奏管理

## 文档阅读路径

如果你是第一次接触项目，建议按以下顺序阅读：

1. [快速开始](./Quick-Start.md)
2. [功能总览](./Feature-Guide.md)
3. [核心技术实现](./Technical-Architecture.md)
4. [数据文件与路径](./Data-and-Paths.md)
5. [构建与发布](./Build-and-Release.md)
6. [常见问题与排障](./Troubleshooting.md)
7. [FAQ](./FAQ.md)

## 当前导航与功能映射

1. Codex CLI状态  
2. VSCode Codex  
3. config.toml配置  
4. opencode 配置  
5. 多账号切换  
6. Codex会话管理  
7. Skill 管理  
8. 中转站接口  
9. OpenAI官网状态  
10. 检查更新

上述导航顺序已在功能文档中逐一对应，便于“界面操作”与“技术原理”互相对照。

## 代码结构（总览）

- `pyside_switcher.py`：UI 页面、交互编排、后台线程调度
- `codex_switcher.py`：共享能力（账号存储、配置写入、探测与工具函数）
- `build.ps1`：标准构建入口
- `codex_switcher.spec`：PyInstaller 打包定义
- `tests/`：关键行为回归测试（如 apikey 补丁链路）

## 设计原则

- **可回退优先**：涉及插件/配置写入的关键操作，默认先备份再变更。
- **关键失败即中止**：对核心规则写入失败，不做“看似成功”的降级。
- **诊断可解释**：诊断不仅给结论，还提供可复现的细节信息。
- **UI 不阻塞**：耗时任务在后台线程执行，主线程仅负责渲染和交互。
