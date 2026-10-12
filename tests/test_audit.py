"""审计完整性：高危动作审批-执行全链路留痕，可追溯（源文档 2.3 审计要求）。"""
import json
import tempfile
import unittest
from pathlib import Path

from helpers import approve_all, cli, drive_to_success, jout

HIGH_RISK = ("send_screenshot", "ios_sign", "upload_store")


class AuditTest(unittest.TestCase):
    def test_audit_trail_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            base, task = Path(tmp), "task-audit"
            approve_all(base, task)
            drive_to_success(base, task)
            audits = jout(cli(base, "audit", "--task", task))["audits"]
            self.assertTrue(audits, "必须有审计记录")
            actions = [(a["step"], a["action"]) for a in audits]
            for step in HIGH_RISK:
                self.assertIn((step, "APPROVE"), actions, f"{step} 缺放行记录")
                self.assertIn((step, "EXEC"), actions, f"{step} 缺执行记录")
            self.assertIn(("wait_customer_confirm", "CONFIRM_REPLY"), actions)
            # 普通步骤执行也留痕：gen_config 应有 EXEC
            self.assertIn(("gen_config", "EXEC"), actions)


if __name__ == "__main__":
    unittest.main()
