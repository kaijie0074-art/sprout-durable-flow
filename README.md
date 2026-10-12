# sprout-durable-flow

> **长任务的状态与恢复**（问题⑧）
>
> 横跨数小时~数天的多步骤交付流水线，任意一步被 `kill -9` / 超时 / 人工介入后：**任务可从断点恢复、已发生的副作用不重放、未完成的高危动作不被跳过或重放**。

![CI](https://github.com/kaijie0074-art/sprout-durable-flow/actions/workflows/ci.yml/badge.svg)

## 对应岗位难题

末龙科技 AI 应用工程师岗·三大技术难题之**问题⑧**。矛盾：对话式 Agent（OpenClaw）是**个人会话模型**——没有 workflow 持久化、断点恢复、幂等语义、补偿事务，拿它当执行骨架等于"拿聊天记录当数据库"。核心架构决策：**把执行从会话里剥离出来**，交互层只做触发/汇报/审批，执行层是显式状态机。

## 架构

```mermaid
flowchart LR
    subgraph 交互层["交互层（OpenClaw/IM，本仓库之外）"]
        A[对话触发任务] -->|创建任务| B
        B[进度汇报 / 审批问答] <--|事件通知| W
    end
    subgraph 执行层["执行层（本仓库）"]
        B["SQLite 任务表<br/>状态/游标/版本/审计"]
        C["状态机引擎<br/>三段式步骤"]
        D["幂等键去重<br/>journal 前置探测"]
        E["高危审批门<br/>外发/签名/上传/付费"]
        F["CAS 并发控制"]
        G["看门狗<br/>超时重入+告警"]
        B --> C --> D
        C --> E
        C --> F
        G --> B
    end
    C -->|副作用| H["Mock 外部服务<br/>渲染/微信/签名机/商店后台"]
```

三段式步骤（源文档 2.3）：
1. **前置探测**（幂等检查）：这一步的产物/副作用是否已存在？已做即跳过（留 SKIP 审计）；
2. **执行**：产出副作用 + 幂等键落库；
3. **确认**：写检查点，推进游标。

## 快速开始

```bash
# 跑一条完整流水线（会在等客户确认处停下）
python3 -m durable_flow.cli --base-dir ./demo run --task t1

# 客户三天后回复——每条命令都是新进程（=原会话早已蒸发），凭任务 ID 续跑
python3 -m durable_flow.cli --base-dir ./demo confirm --task t1 --reply "可以了"
python3 -m durable_flow.cli --base-dir ./demo run --task t1

# 故障注入：在该步 execute 后、confirm 前 SIGKILL 自身，再恢复
python3 -m durable_flow.cli --base-dir ./demo2 run --task t2 --crash-at ios_sign
python3 -m durable_flow.cli --base-dir ./demo2 run --task t2   # 从断点恢复，副作用不重放

# 高危审批 / 看门狗 / CAS / 审计
python3 -m durable_flow.cli --base-dir ./demo approve --task t1 --step ios_sign
python3 -m durable_flow.cli --base-dir ./demo watchdog --stale 3600
python3 -m durable_flow.cli --base-dir ./demo cas --project p1 --expected-version 0 --data '{"theme":"blue"}'
python3 -m durable_flow.cli --base-dir ./demo audit --task t1
```

## 验证

```bash
python3 -m unittest discover -s tests -v   # 11 项全绿（约 30s，含 8 步逐一 SIGKILL 注入）
```

同一套测试在缺陷基线上 **18 项失败**（docs/06）：崩溃后从头重放副作用、无等待状态、无审批门、CAS 后写覆盖、无看门狗、无审计。

## 目录结构

```
durable_flow/
  store.py       # SQLite：任务表/检查点/幂等去重/审计/CAS 配置（WAL）
  engine.py      # 状态机执行器：三段式 + 审批门 + waiting_human + 故障注入
  steps.py       # 八步交付流水线（源文档 2.1）与幂等锚点
  services.py    # Mock 外部服务：journal 副作用日志 + 幂等探测查询
  watchdog.py    # 超时任务重入 + 长等待告警事件
  cli.py         # run/approve/confirm/status/audit/watchdog/cas
tests/           # 11 项回归（子进程真实 SIGKILL 注入）
docs/            # 问题报告 / 复现 / 调研 / 设计 / 覆盖 / 回归
```

## 工程过程

问题报告见 [Issue #1](https://github.com/kaijie0074-art/sprout-durable-flow/issues/1)，修复走 [PR #2](https://github.com/kaijie0074-art/sprout-durable-flow/pull/2)（评审/采纳/验收），发布见 [Releases](https://github.com/kaijie0074-art/sprout-durable-flow/releases)。

## 声明

**模拟项目**：为末龙科技 AI 应用工程师岗三大技术难题的工程化模拟（2026-10）。外部系统（渲染服务/微信/签名机/商店后台）全部为确定性本地 Mock；OpenClaw 仅作为交互层概念引用，本仓库实现的是与它解耦的执行层。
