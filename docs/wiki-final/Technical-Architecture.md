# 核心技术实现

- 适用版本：`v2.0.6`
- 最后更新：`2026-02-07`

本页聚焦关键链路、失败策略与设计原因。

## 1. 模块职责分离

### UI 层：`pyside_switcher.py`

负责：

- 页面构建与交互
- 耗时任务调度
- 页面间状态同步

### 能力层：`codex_switcher.py`

负责：

- 账号与配置文件读写
- 命令查找与调用
- 网络探测与日志记录

这种分离使“业务能力”可复用，“界面”可演进。

## 2. 并发模型

- 耗时任务（网络/扫描/版本查询）在后台线程执行
- UI 更新统一通过 `run_in_ui(...)` 回主线程

优势：

- 避免主界面卡死
- 减少线程安全问题

## 3. 账号应用链路（写入原理）

“应用账号”会触发完整落盘流程：

1. `config.toml` 更新 provider `base_url`
2. `auth.json` 更新 `OPENAI_API_KEY`
3. Team 场景写入 `OPENAI_ORG_ID`，非 Team 场景清理
4. `codex_profiles.json` 更新 active 指针

在 Windows 下，`safe_write_text(...)` 会处理隐藏/只读属性，提升写入可靠性。

## 4. 接口诊断算法

入口：`probe_endpoints(...)`

分层执行：

- 网络层：Ping / HEAD / 443 端口
- 接口层：多端点请求（含 `/v1` 变体）
- 语义层：响应结构校验

输出不仅有“成败”，还会给出可解释的失败原因，便于快速归因。

## 5. VSCode 增模补丁链路

入口：`VSCodePluginPage.apply_patch()`

补丁顺序：

1. `_apply_allowlist_patch`（关键）
2. `_apply_apikey_filter_patch`（关键）
3. `_apply_apikey_order_inject_patch`（增强）
4. `_apply_initial_data_patch`（增强）

失败策略：

- 关键补丁失败：中止写入并报错
- 增强补丁失败：允许写入，但提示“部分规则未更新”

写入前先备份，保证可回滚。

## 6. 会话检索策略

入口：`SessionManagerPage.apply_filter()`

- 第一阶段：用 `history.jsonl` 快速检索
- 第二阶段：无命中则回退深度扫描原始会话文件

可配置项：

- AND/OR 模式
- 最近天数
- 最大扫描数量
- 取消搜索

## 7. VS Code 会话继续策略

流程采用“能力优先 + 回退保障”：

1. 尝试 URI 直达指定会话
2. 若失败，回退为打开对应工作目录

这样既兼顾最佳体验，也保证最低可用性。

## 8. 更新检查与导航徽标

更新页获取最新版本并计算版本差异数量，再将数量映射到导航红点，实现“后台检查 + 前台可见提醒”的闭环。
