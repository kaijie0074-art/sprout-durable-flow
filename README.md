# sprout-durable-flow（缺陷基线 v0.1.0）

> ⚠️ **缺陷基线快照**：把个人会话当执行骨架的 naive 顺序执行器，用于复现问题报告的 7 类失败场景。

## 现状

- 进度写非结构化 `memory.txt`（无事务性，崩溃/升级后读不懂）；
- 无检查点、无幂等：崩溃后"恢复"= 从头重跑，副作用全部重放；
- 无审批门（签名/上传/外发直接执行）、无 CAS（后写覆盖）、无看门狗、无审计；
- 等客户确认没有状态：会话超时即上下文全丢。

## 复现

```bash
python3 -m unittest discover -s tests
# Ran 11 tests ... FAILED (failures=18)
```

失败清单与失败场景对位见主仓库 `docs/02-复现材料.md`；修复版见 [sprout-durable-flow](../sprout-durable-flow)（v1.0.0，11/11 通过）。
