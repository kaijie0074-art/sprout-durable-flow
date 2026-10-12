"""评审轮修复验证（PR #2 评审意见 R2-1/R2-2 的回归测试）。"""
import json
import tempfile
import unittest
from pathlib import Path

from helpers import approve_all, cli, events, journal_entries, jout

class TestReviewFixes(unittest.TestCase):
    def test_r2_1_alerts_stale_waiting_approval(self):
        """R2-1：审批悬置超时必须告警，且不得自动重入（门不能被狗绕过）。"""
        with tempfile.TemporaryDirectory() as tmp:
            base, task = Path(tmp), "task-r21"
            st = jout(cli(base, "run", "--task", task))       # 停在 send_screenshot 审批门
            self.assertEqual(st["status"], "waiting_approval")
            w = jout(cli(base, "watchdog", "--stale", "-1"))
            self.assertIn(task, w["approval_alerts"], "审批悬置必须出现在告警里")
            self.assertNotIn(task, w["requeued"], "审批门状态不得被自动重入")
            types = [e["type"] for e in events(base)]
            self.assertIn("alert_approval_pending", types)
            # 门未被绕过：任务仍是 waiting_approval，副作用仍未发生
            st2 = jout(cli(base, "status", "--task", task))
            self.assertEqual(st2["status"], "waiting_approval")
            # 门未被绕过：任务仍是 waiting_approval，门外发副作用未发生
            # （前两步 gen_config/render_preview 的副作用是合法已确认的）
            st2 = jout(cli(base, "status", "--task", task))
            self.assertEqual(st2["status"], "waiting_approval")
            sends = [e for e in journal_entries(base)
                     if (e["service"], e["action"]) == ("wecom", "send_message")]
            self.assertEqual(len(sends), 0, "未审批不得外发客户消息")

    def test_r2_2_cas_conflict_rolls_back_atomically(self):
        """R2-2：CAS 冲突路径在事务内回滚，不留下半写状态。"""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            r1 = cli(base, "cas", "--project", "p", "--expected-version", "0",
                     "--data", '{"a":1}')
            self.assertEqual(json.loads(r1.stdout)["version"], 1)
            r2 = cli(base, "cas", "--project", "p", "--expected-version", "0",
                     "--data", '{"a":2}')
            self.assertEqual(r2.returncode, 1)
            # 冲突回滚后，基于真实最新版本（1）的写入仍然成功——库未被破坏
            r3 = cli(base, "cas", "--project", "p", "--expected-version", "1",
                     "--data", '{"a":3}')
            self.assertEqual(json.loads(r3.stdout)["version"], 2)


if __name__ == "__main__":
    unittest.main()
