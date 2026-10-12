"""核心验收：任意一步 SIGKILL 后恢复，断点续跑且副作用零重放（源文档 2.5 判据一）。"""
import tempfile
import unittest
from pathlib import Path

from helpers import approve_all, drive, drive_to_success, journal_entries

ALL_STEPS = ["gen_config", "render_preview", "send_screenshot", "wait_customer_confirm",
             "android_build", "ios_sign", "build_submit_materials", "upload_store"]

MUST_SIDEEFFECTS = [
    ("agent", "gen_config"), ("renderer", "render_preview"), ("wecom", "send_message"),
    ("builder", "android_build"), ("signer", "sign_ios"), ("store", "upload"),
]


class CrashRecoveryTest(unittest.TestCase):
    def test_crash_at_every_step_resumes_without_replay(self):
        """对全部 8 步逐一注入『execute 后、confirm 前』SIGKILL，恢复后：
        任务到 succeeded；副作用键无一重复；关键副作用一个不少。"""
        for step in ALL_STEPS:
            with self.subTest(step=step):
                with tempfile.TemporaryDirectory() as tmp:
                    base = Path(tmp)
                    task = "task-crash"
                    approve_all(base, task)          # 高危步骤预先放行
                    r1 = drive(base, task, crash_at=step)
                    self.assertTrue(r1["killed"], f"{step} 注入应使进程被杀")

                    st = drive_to_success(base, task)
                    self.assertEqual(st["status"], "succeeded", f"{step} 恢复失败")

                    entries = [e for e in journal_entries(base) if e["service"] != "customer"]
                    keys = [(e["service"], e["action"], e["key"]) for e in entries]
                    self.assertEqual(len(keys), len(set(keys)),
                                     f"{step} 场景副作用重放：{keys}")
                    anchors = {(e["service"], e["action"]) for e in entries}
                    for need in MUST_SIDEEFFECTS:
                        self.assertIn(need, anchors, f"{step} 场景缺关键副作用 {need}")


if __name__ == "__main__":
    unittest.main()
