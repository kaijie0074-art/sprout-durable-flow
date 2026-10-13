"""显式状态机执行器：三段式步骤（前置探测 → 执行 → 确认检查点）。

崩溃语义：故障注入点固定在「execute 之后、confirm 之前」——即副作用
已发生但检查点未写。这是恢复最难的场景：重启后靠前置探测识别"已做
过"并跳过，保证副作用不重放（源文档 2.5 验收判据第一条）。

深潜研究（2026-10-14）补强：
  - 副作用恰好一次窗口：适配器派发前落 #inflight WAL，恢复时"有 inflight
    无确认"= 结果不确定 → 挂起人工裁决（approve --resolution），机器永不
    自动重发不确定副作用（docs/research/deep-dive-20261014.md Q1/Q2）；
  - 单任务单执行者：per-task flock 互斥，杜绝并发 probe-probe 竞态（Q3）。
"""
import os
import signal
from pathlib import Path

from .services import INFLIGHT_SUFFIX, make_services
from .steps import (HIGH_RISK_STEPS, STEPS, STEP_NAMES, journal_anchor,
                    probe_key, run_step)
from .store import Store
from .locks import TaskBusyError, task_lock   # noqa: F401  (TaskBusyError 供 CLI/上层捕获)


class Engine:
    def __init__(self, base_dir):
        self.base = Path(base_dir)
        self.store = Store(self.base / "durable.db")
        self.svc = make_services(self.base)

    # ------------------------------------------------------------------
    def run(self, task_id: str, project: str = "default-project", crash_at: str = None) -> dict:
        """从最后确认步重入；也用作 resume（同一语义：状态都在库里）。

        同一任务同一时刻单执行者（flock）：并发 run 第二个直接 TaskBusyError，
        杜绝 probe-probe 竞态导致的静默副作用重复。"""
        task = self.store.create_task(task_id, project)
        if task["status"] == "succeeded":
            return self._snap(task, noop=True)
        with task_lock(self.base, task_id):
            return self._run_locked(task_id, crash_at)

    def _run_locked(self, task_id: str, crash_at: str = None) -> dict:
        task = self.store.get_task(task_id)
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

            # —— 前置探测：副作用已确认存在则跳过（幂等防线）——
            service, action = journal_anchor(name)
            confirmed = (service is None or
                         self.svc["journal"].has(service, action, key) or
                         self.store.is_done(task_id, name, key))
            # 恰好一次窗口：适配器已落 #inflight（派发边界）但确认未落账
            # ——外部效果可能已送达。机器不得自动重发；交人工裁决。
            uncertain = (service is not None and not confirmed and
                         self.svc["journal"].has(service, action + INFLIGHT_SUFFIX, key) and
                         not self.store.has_audit(task_id, name, "UNCERTAIN_RESEND"))
            resolved = self.store.has_audit(task_id, name, "UNCERTAIN_RESOLVED")
            if uncertain and not resolved:
                if not self.store.has_audit(task_id, name, "UNCERTAIN"):
                    self.store.audit(task_id, name, "UNCERTAIN",
                                     "崩溃于副作用确认前（#inflight 已落账），"
                                     "结果不确定，禁止自动重发")
                self.store.update_task(task_id, status="waiting_approval",
                                       current_step=name)
                return self._snap(self.store.get_task(task_id))

            already = True
            if uncertain:   # 人工裁决 assume_done：视为已完成，不重发
                self.store.mark_done(task_id, name, key)
                self.store.audit(task_id, name, "SKIP", "人工裁决：不确定结果已核实，跳过重发")
            elif confirmed:
                self.store.audit(task_id, name, "SKIP", "前置探测：已存在，跳过执行")
            else:
                already = False
                run_step(name, self.svc, key, self.base)      # 执行副作用（适配器自落 #inflight）
                self.store.mark_done(task_id, name, key)
                self.store.audit(task_id, name, "EXEC", f"幂等键 {key}")
                self._maybe_crash(crash_at, name)             # 注入点：execute 后、confirm 前

            # —— 确认：写检查点，推进游标 ——
            self._confirm(task_id, name, {"key": key, "skipped": already}, cursor)
            cursor += 1

        self.store.update_task(task_id, status="succeeded", current_step=None)
        return self._snap(self.store.get_task(task_id))

    # ------------------------------------------------------------------
    def approve(self, task_id: str, step: str, resolution: str = "assume_done") -> dict:
        """高危动作放行 / 不确定结果裁决：写审计，状态回到可执行。

        UNCERTAIN 步骤的两种人工裁决（凭对外部系统的核实，机器不可代判）：
          assume_done  副作用已发生 → 视为已完成，不重发（at-most-once）
          resend       副作用未送达 → 允许重发（人工担责 at-least-once）
        """
        uncertain = self.store.has_audit(task_id, step, "UNCERTAIN")
        if step not in HIGH_RISK_STEPS and not uncertain:
            raise ValueError(f"{step} 不是高危步骤，无需审批")
        if uncertain:
            if resolution == "resend":
                self.store.audit(task_id, step, "UNCERTAIN_RESEND",
                                 "人工核实未送达，允许重发")
            elif not self.store.has_audit(task_id, step, "UNCERTAIN_RESOLVED"):
                self.store.mark_done(task_id, step, probe_key(task_id, step))
                self.store.audit(task_id, step, "UNCERTAIN_RESOLVED",
                                 "人工核实：视为已完成，不重发")
            else:
                self.store.audit(task_id, step, "APPROVE", "重复放行（幂等）")
        else:
            self.store.audit(task_id, step, "APPROVE", "负责人放行")
        task = self.store.get_task(task_id)
        if task and task["status"] == "waiting_approval":
            self.store.update_task(task_id, status="pending")
        return {"task_id": task_id, "approved": step, "resolution": resolution}

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
            os.kill(os.getpid(), signal.SIGKILL)   # 崩溃注入：不给清理机会

    def _snap(self, task, noop: bool = False) -> dict:
        return {
            "task_id": task["task_id"],
            "status": task["status"],
            "current_step": task["current_step"],
            "step_cursor": task["step_cursor"],
            "done_steps": self.store.done_steps(task["task_id"]),
            "noop": noop,
        }
