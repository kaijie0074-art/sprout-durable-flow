"""显式状态机执行器：三段式步骤（前置探测 → 执行 → 确认检查点）。

崩溃语义：故障注入点固定在「execute 之后、confirm 之前」——即副作用
已发生但检查点未写。这是恢复最难的场景：重启后靠前置探测识别"已做
过"并跳过，保证副作用不重放（源文档 2.5 验收判据第一条）。
"""
import os
import signal
from pathlib import Path

from .services import make_services
from .steps import (HIGH_RISK_STEPS, STEPS, STEP_NAMES, journal_anchor,
                    probe_key, run_step)
from .store import Store


class Engine:
    def __init__(self, base_dir):
        self.base = Path(base_dir)
        self.store = Store(self.base / "durable.db")
        self.svc = make_services(self.base)

    # ------------------------------------------------------------------
    def run(self, task_id: str, project: str = "default-project", crash_at: str = None) -> dict:
        """从最后确认步重入；也用作 resume（同一语义：状态都在库里）。"""
        task = self.store.create_task(task_id, project)
        if task["status"] == "succeeded":
            return self._snap(task, noop=True)
        self.store.update_task(task_id, status="running")

        cursor = task["step_cursor"]
        while cursor < len(STEPS):
            step = STEPS[cursor]
            name = step["name"]
            key = probe_key(task_id, name)

            # —— 人工等待步：确认过（audit 有 CONFIRM_REPLY）则推进，否则挂起 ——
            if step["risk"] == "human_wait":
                if self.store.has_audit(task_id, name, "CONFIRM_REPLY"):
                    self._confirm(task_id, name, {"confirmed": True}, cursor)
                    cursor += 1
                    continue
                self.store.update_task(task_id, status="waiting_human", current_step=name)
                self._maybe_crash(crash_at, name)   # 注入点：副作用（置状态）后、推进前
                return self._snap(self.store.get_task(task_id))

            # —— 高危审批门：无 APPROVE 记录不得执行，也不得跳过 ——
            if step["risk"] == "high" and not self.store.has_audit(task_id, name, "APPROVE"):
                self.store.update_task(task_id, status="waiting_approval", current_step=name)
                return self._snap(self.store.get_task(task_id))

            # —— 前置探测：副作用已存在（上次 execute 后崩溃/人工已做）即跳过 ——
            service, action = journal_anchor(name)
            already = (service is None or
                       self.svc["journal"].has(service, action, key) or
                       self.store.is_done(task_id, name, key))
            if already:
                self.store.audit(task_id, name, "SKIP", "前置探测：已存在，跳过执行")
            else:
                run_step(name, self.svc, key, self.base)      # 执行副作用
                self.store.mark_done(task_id, name, key)
                self.store.audit(task_id, name, "EXEC", f"幂等键 {key}")
                self._maybe_crash(crash_at, name)             # 注入点：execute 后、confirm 前

            # —— 确认：写检查点，推进游标 ——
            self._confirm(task_id, name, {"key": key, "skipped": already}, cursor)
            cursor += 1

        self.store.update_task(task_id, status="succeeded", current_step=None)
        return self._snap(self.store.get_task(task_id))

    # ------------------------------------------------------------------
    def approve(self, task_id: str, step: str) -> dict:
        """高危动作放行：写审计，状态回到可执行。"""
        if step not in HIGH_RISK_STEPS:
            raise ValueError(f"{step} 不是高危步骤，无需审批")
        self.store.audit(task_id, step, "APPROVE", "负责人放行")
        task = self.store.get_task(task_id)
        if task and task["status"] == "waiting_approval":
            self.store.update_task(task_id, status="pending")
        return {"task_id": task_id, "approved": step}

    def confirm_customer(self, task_id: str, reply: str) -> dict:
        """客户回复落库（与原会话无关，凭任务 ID 关联）。幂等：重复回复只记一次。"""
        journal = self.svc["journal"]
        key = probe_key(task_id, "wait_customer_confirm")
        if not journal.has("customer", "reply", key):
            journal.append("customer", "reply", key, {"reply": reply})
        self.store.audit(task_id, "wait_customer_confirm", "CONFIRM_REPLY", reply[:80])
        task = self.store.get_task(task_id)
        if task and task["status"] == "waiting_human":
            self.store.update_task(task_id, status="pending")
        return {"task_id": task_id, "confirmed": True, "reply": reply}

    # ------------------------------------------------------------------
    def _confirm(self, task_id: str, step: str, state: dict, cursor: int):
        self.store.save_checkpoint(task_id, step, state)
        self.store.update_task(task_id, step_cursor=cursor + 1, current_step=None)

    def _maybe_crash(self, crash_at: str | None, step: str):
        if crash_at == step:
            os.kill(os.getpid(), signal.SIGKILL)   # 模拟进程被杀：不给清理机会

    def _snap(self, task, noop: bool = False) -> dict:
        return {
            "task_id": task["task_id"],
            "status": task["status"],
            "current_step": task["current_step"],
            "step_cursor": task["step_cursor"],
            "done_steps": self.store.done_steps(task["task_id"]),
            "noop": noop,
        }
