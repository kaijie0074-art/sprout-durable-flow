"""验收判据四：已完成任务恢复为 no-op；人工已完成的动作不重放。"""
import json
import tempfile
import unittest
from pathlib import Path

from helpers import approve_all, cli, drive_to_success, journal_entries


class NoReplayTest(unittest.TestCase):
    def test_resume_completed_task_is_noop(self):
        """任务 succeeded 后再 run：状态 no-op，副作用零新增。"""
        with tempfile.TemporaryDirectory() as tmp:
            base, task = Path(tmp), "task-done"
            approve_all(base, task)
            drive_to_success(base, task)
            before = len(journal_entries(base))
            st = json.loads(cli(base, "run", "--task", task).stdout)
            self.assertEqual(st["status"], "succeeded")
            self.assertTrue(st.get("noop"), "已完成任务恢复必须是 no-op")
            self.assertEqual(len(journal_entries(base)), before, "no-op 不得新增副作用")

    def test_manually_done_upload_not_replayed(self):
        """『提审材料其实已人工传完』：journal 已有该键 → 引擎前置探测跳过，不得重复上传。"""
        with tempfile.TemporaryDirectory() as tmp:
            base, task = Path(tmp), "task-manual"
            approve_all(base, task)
            key = f"{task}:upload_store"
            jpath = Path(base) / "logs" / "journal.jsonl"
            jpath.parent.mkdir(parents=True, exist_ok=True)
            with jpath.open("a", encoding="utf-8") as f:   # 模拟人工已上传
                f.write(json.dumps({"ts": 0, "service": "store", "action": "upload",
                                    "key": key, "payload": {"version": "1.0.0"}},
                                   ensure_ascii=False) + "\n")
            drive_to_success(base, task)
            uploads = [e for e in journal_entries(base)
                       if (e["service"], e["action"]) == ("store", "upload")]
            self.assertEqual(len(uploads), 1, "人工已完成的动作不得重放")
            audits = json.loads(cli(base, "audit", "--task", task).stdout)["audits"]
            self.assertIn(("upload_store", "SKIP"),
                          [(a["step"], a["action"]) for a in audits],
                          "跳过必须留审计（SKIP），可追溯")


if __name__ == "__main__":
    unittest.main()
