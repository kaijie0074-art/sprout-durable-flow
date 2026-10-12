"""验收判据二：等待期会话蒸发不影响任务续跑（waiting_human 跨进程存活）。"""
import tempfile
import unittest
from pathlib import Path

from helpers import approve_all, cli, drive_to_success, jout, journal_entries


class WaitingHumanTest(unittest.TestCase):
    def test_wait_state_persists_across_processes(self):
        with tempfile.TemporaryDirectory() as tmp:
            base, task = Path(tmp), "task-wait"
            approve_all(base, task)
            st = jout(cli(base, "run", "--task", task))
            self.assertEqual(st["status"], "waiting_human")
            self.assertEqual(st["current_step"], "wait_customer_confirm")
            self.assertEqual(st["step_cursor"], 3, "前三步应已完成并确认")
            sends = [e for e in journal_entries(base)
                     if (e["service"], e["action"]) == ("wecom", "send_message")]
            self.assertEqual(len(sends), 1, "预览截图应恰好发给客户一次")

            # 每条 CLI 都是新进程=原会话早已蒸发；凭任务 ID 确认并续跑
            confirmed = jout(cli(base, "confirm", "--task", task, "--reply", "可以了"))
            self.assertTrue(confirmed["confirmed"])
            st2 = drive_to_success(base, task)
            self.assertEqual(st2["status"], "succeeded")
            sends2 = [e for e in journal_entries(base)
                      if (e["service"], e["action"]) == ("wecom", "send_message")]
            self.assertEqual(len(sends2), 1, "续跑后不得向客户重发消息")

    def test_duplicate_confirm_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            base, task = Path(tmp), "task-wait2"
            approve_all(base, task)
            cli(base, "run", "--task", task)
            cli(base, "confirm", "--task", task, "--reply", "可以")
            cli(base, "confirm", "--task", task, "--reply", "可以")  # 重复确认
            replies = [e for e in journal_entries(base) if e["service"] == "customer"]
            self.assertEqual(len(replies), 1, "重复确认只落一条记录")


if __name__ == "__main__":
    unittest.main()
