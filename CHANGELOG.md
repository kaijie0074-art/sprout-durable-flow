# 变更日志

遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

## [v1.0.0] - 2026-10-13

修复问题报告（Issue #1）全部 7 类失败场景，11/11 测试通过（含 8 步逐一 SIGKILL 注入恢复）。

### 新增
- **SQLite 任务存储** `store.py`：任务表（状态/游标）、检查点、幂等去重表、审计日志、CAS 版本化配置；WAL 模式保证任意步 SIGKILL 后已提交事务不丢。
- **状态机引擎** `engine.py`：三段式步骤（前置探测→执行→确认检查点）；`waiting_human` 状态凭任务 ID 关联，与原会话无关；高危审批门（无 APPROVE 记录不得执行也不得跳过）；已完成任务恢复为 no-op；故障注入（`--crash-at` 在 execute 后、confirm 前 SIGKILL）。
- **幂等防线**：幂等键（task:step）+ journal 前置探测双保险；SKIP 留审计可追溯。
- **Mock 外部服务** `services.py`：微信/渲染/签名机/商店后台，副作用 append-only 落 journal.jsonl，兼作幂等探测查询。
- **看门狗** `watchdog.py`：running 超时自动重入 + waiting_human 长等待告警事件。
- **CLI**：run/approve/confirm/status/audit/watchdog/cas，JSON 输出，与基线共用命令面。
- 测试 11 项：逐步 SIGKILL 恢复、跨进程等待续跑、审批门、人工已完成不重放、CAS 冲突、看门狗、审计完整性；CI（Python 3.11）。

## [v0.1.0] - 2026-10-13

缺陷基线（现状快照）：个人会话当执行骨架的 naive 顺序执行器。

- 进度写非结构化 `memory.txt`；无检查点无幂等，崩溃恢复=从头重放全部副作用；
- 无审批门（签名/上传/外发直接执行）；配置写入 last-write-wins；无看门狗；无审计。
- 同一套测试在基线上 18 项失败——复现材料（docs/02）。
