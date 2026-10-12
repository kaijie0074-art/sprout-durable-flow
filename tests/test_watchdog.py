"""看门狗：中断任务自动重入 + 长等待告警事件（无人值守窗口的基本可用性）。"""
import json
import tempfile
import unittest
from pathlib import Path

from helpers import approve_all, cli, drive, drive_to_success, events, jout


class WatchdogTest(unittest.TestCase):
    def test_requeues_crashed_running_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            base, task = Path(tmp), "task-wd"
            approve_all(base, task)
            r = drive(base, task, crash_at="android_build")
            self.assertTrue(r["killed"], "注入应使进程中断在 running")

            w = jout(cli(base, "watchdog", "--stale", "-1"))   # -1 = 立即视为超时
            self.assertIn(task, w["requeued"], "中断任务应被重新入队")
            types = [e["type"] for e in events(base)]
            self.assertIn("requeue", types, "重入必须出告警事件供 IM 推送")

            st = jout(cli(base, "status", "--task", task))
            self.assertEqual(st["status"], "pending")
            st2 = drive_to_success(base, task)                 # 重入后可续跑成功
            self.assertEqual(st2["status"], "succeeded")

    def test_alerts_stale_waiting_human(self):
        with tempfile.TemporaryDirectory() as tmp:
            base, task = Path(tmp), "task-wd2"
            approve_all(base, task)
            cli(base, "run", "--task", task)                    # 停在 waiting_human
            w = jout(cli(base, "watchdog", "--stale", "-1"))
            self.assertIn(task, w["alerts"], "『等客户三天』无进展必须告警")
            alerts = [e for e in events(base) if e["type"] == "alert_still_waiting"]
            self.assertEqual(len(alerts), 1)


if __name__ == "__main__":
    unittest.main()
