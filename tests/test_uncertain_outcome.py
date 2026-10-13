"""深潜 Q2 回归：#inflight 派发边界 WAL + 不确定结果人工裁决。

覆盖三个语义：
  1. 派发后崩溃（#inflight 已落账、确认未落账）→ 恢复不得自动重发，
     必须停在 waiting_approval 并写 UNCERTAIN 审计；
  2. 人工裁决 assume_done → 视为已完成（journal 永不出现确认条目）；
  3. 人工裁决 resend → 允许重发（journal 出现确认条目）；
  4. 检查点幂等：同一步重复确认不产生重复 checkpoint（done_steps 无重复）。
"""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from durable_flow.services import Journal
from helpers import cli, drive_to_success, jout


class UncertainOutcomeTest(unittest.TestCase):
    def _uncertain_state(self, base, task, step, service, action):
        """构造"崩溃于派发后、确认前"的 journal 残留态（等价矩阵 mid_exec 格）。"""
        j = Journal(Path(base) / "logs")
        j.mark_inflight(service, action, f"{task}:{step}")

    def test_midflight_crash_halts_and_marks_uncertain(self):
        with tempfile.TemporaryDirectory() as tmp:
            base, task = Path(tmp), "t-uncertain"
            self._uncertain_state(base, task, "gen_config", "agent", "gen_config")
            st = jout(cli(base, "run", "--task", task))
            self.assertEqual(st["status"], "waiting_approval")
            self.assertEqual(st["current_step"], "gen_config")
            audits = jout(cli(base, "audit", "--task", task))["audits"]
            self.assertTrue(any(a["action"] == "UNCERTAIN" for a in audits),
                            f"应写 UNCERTAIN 审计: {audits}")

    def test_assume_done_never_re_sends(self):
        with tempfile.TemporaryDirectory() as tmp:
            base, task = Path(tmp), "t-assume"
            self._uncertain_state(base, task, "gen_config", "agent", "gen_config")
            cli(base, "run", "--task", task)                    # → waiting_approval
            out = jout(cli(base, "approve", "--task", task, "--step", "gen_config"))
            self.assertEqual(out["resolution"], "assume_done")
            st = drive_to_success(base, task)
            self.assertEqual(st["status"], "succeeded")
            # 机器未重发：gen_config 确认条目从未出现
            j = Journal(Path(base) / "logs")
            self.assertEqual(j.entries("agent", "gen_config", f"{task}:gen_config"), [])

    def test_resend_allows_one_re_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            base, task = Path(tmp), "t-resend"
            self._uncertain_state(base, task, "render_preview", "renderer", "render_preview")
            st = jout(cli(base, "run", "--task", task))         # gen_config EXEC 后 → uncertain
            self.assertEqual(st["status"], "waiting_approval")
            out = jout(cli(base, "approve", "--task", task, "--step", "render_preview",
                           "--resolution", "resend"))
            self.assertEqual(out["resolution"], "resend")
            st = drive_to_success(base, task)
            self.assertEqual(st["status"], "succeeded")
            j = Journal(Path(base) / "logs")
            confirms = j.entries("renderer", "render_preview", f"{task}:render_preview")
            self.assertEqual(len(confirms), 1, "resend 后恰好执行一次")

    def test_checkpoint_reconfirmation_is_idempotent(self):
        """模拟"checkpoint 已写、游标未推进"的恢复：重复确认不产生重复步。"""
        with tempfile.TemporaryDirectory() as tmp:
            base, task = Path(tmp), "t-ckpt"
            st = drive_to_success(base, task)
            self.assertEqual(st["status"], "succeeded")
            conn = sqlite3.connect(Path(base) / "durable.db")
            # 回退游标到 0 并重置状态，模拟崩溃恢复点：全步重新确认
            conn.execute("UPDATE tasks SET step_cursor=0, status='pending', current_step=NULL")
            conn.commit()
            conn.close()
            st = drive_to_success(base, task)
            self.assertEqual(st["status"], "succeeded")
            self.assertEqual(len(st["done_steps"]), len(set(st["done_steps"])),
                             f"done_steps 不得重复: {st['done_steps']}")
            conn = sqlite3.connect(Path(base) / "durable.db")
            dups = conn.execute(
                "SELECT step, COUNT(*) c FROM checkpoints GROUP BY step HAVING c > 1").fetchall()
            conn.close()
            self.assertEqual(dups, [])


if __name__ == "__main__":
    unittest.main()
