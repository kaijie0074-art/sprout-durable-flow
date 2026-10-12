"""验收判据三：高危动作过审批门——未放行不执行、不跳过；放行有审计。"""
import tempfile
import unittest
from pathlib import Path

from helpers import cli, jout, journal_entries


class ApprovalGateTest(unittest.TestCase):
    def test_gate_blocks_until_approved(self):
        with tempfile.TemporaryDirectory() as tmp:
            base, task = Path(tmp), "task-gate"
            # 未放行：第一步高危（send_screenshot）前必须停下
            st1 = jout(cli(base, "run", "--task", task))
            self.assertEqual(st1["status"], "waiting_approval")
            self.assertEqual(st1["current_step"], "send_screenshot")
            self.assertEqual(self._sends(base), 0, "未审批不得外发")

            # 再跑也不会偷偷执行
            st2 = jout(cli(base, "run", "--task", task))
            self.assertEqual(st2["status"], "waiting_approval")
            self.assertEqual(self._sends(base), 0)

            # 放行外发 → 走到等待客户 → 确认 → 停在 ios_sign 审批门
            cli(base, "approve", "--task", task, "--step", "send_screenshot")
            st3 = jout(cli(base, "run", "--task", task))
            self.assertEqual(st3["status"], "waiting_human")
            cli(base, "confirm", "--task", task, "--reply", "可以")
            st4 = jout(cli(base, "run", "--task", task))
            self.assertEqual(st4["status"], "waiting_approval")
            self.assertEqual(st4["current_step"], "ios_sign")
            self.assertEqual(self._signs(base), 0, "签名未放行不得执行")

            # 放行签名与上传 → 全链完成
            cli(base, "approve", "--task", task, "--step", "ios_sign")
            st5 = jout(cli(base, "run", "--task", task))
            self.assertEqual((st5["status"], st5["current_step"]),
                             ("waiting_approval", "upload_store"))
            cli(base, "approve", "--task", task, "--step", "upload_store")
            st6 = jout(cli(base, "run", "--task", task))
            self.assertEqual(st6["status"], "succeeded")

            # 审计完整：三次放行、三次高危执行、一次客户确认
            audits = jout(cli(base, "audit", "--task", task))["audits"]
            actions = [(a["step"], a["action"]) for a in audits]
            for step in ("send_screenshot", "ios_sign", "upload_store"):
                self.assertIn((step, "APPROVE"), actions)
                self.assertIn((step, "EXEC"), actions)
            self.assertIn(("wait_customer_confirm", "CONFIRM_REPLY"), actions)

    def test_approve_non_high_risk_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            base, task = Path(tmp), "task-gate2"
            r = cli(base, "approve", "--task", task, "--step", "gen_config")
            self.assertNotEqual(r.returncode, 0, "非高危步骤不应需要/接受审批")

    @staticmethod
    def _sends(base):
        return len([e for e in journal_entries(base)
                    if (e["service"], e["action"]) == ("wecom", "send_message")])

    @staticmethod
    def _signs(base):
        return len([e for e in journal_entries(base)
                    if (e["service"], e["action"]) == ("signer", "sign_ios")])


if __name__ == "__main__":
    unittest.main()
