"""durable_flow —— 长任务的状态与恢复（问题⑧）。

核心架构决策：把"执行"从对话式 Agent（OpenClaw）里剥离出来——
交互层只负责触发/汇报/审批；执行层是显式状态机：
  三段式步骤（前置探测 → 执行 → 确认检查点）+ 幂等键去重
  + 高危审批门 + CAS 并发控制 + 看门狗重入。
"""

__version__ = "1.0.0"

from .engine import Engine  # noqa: F401
from .store import ConfigConflict, Store  # noqa: F401
from .watchdog import scan  # noqa: F401
