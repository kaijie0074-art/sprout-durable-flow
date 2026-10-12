"""【缺陷基线·现状】naive 顺序执行器：把个人会话当执行骨架。

现状问题（docs/01）：
  - 进度写在非结构化 memory.txt（为个人记忆设计，无事务性）；
  - 无检查点、无幂等：崩溃后 resume = 从头重跑，全部副作用重放
    （客户收到两条一样的消息、商店后台出现重复版本）；
  - 无审批门：签名/上传/外发直接执行；
  - 无并发控制：配置写入 last-write-wins；
  - 无看门狗：夜间跑挂没人发现。
"""
import os
import signal
import time
from pathlib import Path

from .services import make_services
from .steps import STEPS, run_step  # 步骤定义与副作用实现复用（问题不在副作用本身）

MEMORY = "memory.txt"


class NaiveRunner:
    def __init__(self, base_dir):
        self.base = Path(base_dir)
        self.svc = make_services(self.base)

    def _write_memory(self, text: str):
        # 现状：进度 = 一行非结构化文本，崩溃/升级后谁也读不懂
        (self.base / MEMORY).write_text(text, encoding="utf-8")

    def run(self, task_id: str, project: str = "default-project", crash_at: str = None) -> dict:
        done = []
        for i, step in enumerate(STEPS):
            name = step["name"]
            self._write_memory(f"当前步骤: {name}（{i + 1}/{len(STEPS)}）")
            if name == "wait_customer_confirm":
                # 现状：在会话里等一句"可以了"就继续——会话超时=上下文全丢
                continue
            run_step(name, self.svc, f"{task_id}:{name}", self.base)  # 无前置探测，直接干
            if crash_at == name:
                os.kill(os.getpid(), signal.SIGKILL)                  # 注入点一致，便于对比
            done.append(name)
        self._write_memory("全部完成")
        return {"task_id": task_id, "status": "done", "done_steps": done,
                "finished_at": round(time.time(), 3)}
