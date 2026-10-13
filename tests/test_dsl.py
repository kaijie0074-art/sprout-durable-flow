"""深潜 Q5 前置：DSL 验证器回归（结构层 S* / 域层 D）。"""
import unittest

from durable_flow.dsl import validate_workflow, to_steps

VALID = {
    "workflow": "app-release",
    "steps": [
        {"name": "gen_config", "risk": "normal", "service": "agent", "action": "gen_config", "depends_on": []},
        {"name": "render_preview", "risk": "normal", "service": "renderer", "action": "render", "depends_on": ["gen_config"]},
        {"name": "send_screenshot", "risk": "high", "service": "wecom", "action": "send", "depends_on": ["render_preview", "gen_config"]},
        {"name": "wait_customer_confirm", "risk": "human_wait", "depends_on": ["send_screenshot"]},
        {"name": "android_build", "risk": "normal", "service": "builder", "action": "build", "depends_on": ["wait_customer_confirm"]},
        {"name": "ios_sign", "risk": "high", "service": "signer", "action": "sign", "depends_on": ["android_build"]},
        {"name": "build_submit_materials", "risk": "normal", "service": "agent", "action": "materials", "depends_on": ["wait_customer_confirm"]},
        {"name": "upload_store", "risk": "high", "service": "store", "action": "upload", "depends_on": ["android_build", "ios_sign", "build_submit_materials"]},
    ],
}


class DslValidatorTest(unittest.TestCase):
    def test_valid_workflow_passes(self):
        self.assertEqual(validate_workflow(VALID), [])
        steps = to_steps(VALID)
        self.assertEqual(len(steps), 8)
        self.assertEqual(steps[3]["service"], None)

    def test_cycle_detected(self):
        doc = {"workflow": "x", "steps": [
            {"name": "a", "risk": "normal", "service": "s", "action": "a", "depends_on": ["b"]},
            {"name": "b", "risk": "normal", "service": "s", "action": "b", "depends_on": ["a"]},
        ]}
        rules = [e["rule"] for e in validate_workflow(doc)]
        self.assertIn("S6", rules)

    def test_unknown_dep_and_dup_name(self):
        doc = {"workflow": "x", "steps": [
            {"name": "a", "risk": "normal", "service": "s", "action": "a", "depends_on": ["ghost"]},
            {"name": "a", "risk": "normal", "service": "s", "action": "a2", "depends_on": []},
        ]}
        rules = [e["rule"] for e in validate_workflow(doc)]
        self.assertIn("S3", rules)
        self.assertIn("S5", rules)

    def test_human_wait_must_not_declare_anchor(self):
        doc = {"workflow": "x", "steps": [
            {"name": "wait_ok", "risk": "human_wait", "service": "x", "action": "y", "depends_on": []},
        ]}
        rules = [e["rule"] for e in validate_workflow(doc)]
        self.assertIn("S9", rules)

    def test_duplicate_anchor_rejected(self):
        doc = {"workflow": "x", "steps": [
            {"name": "a", "risk": "normal", "service": "s", "action": "act", "depends_on": []},
            {"name": "b", "risk": "normal", "service": "s", "action": "act", "depends_on": ["a"]},
        ]}
        rules = [e["rule"] for e in validate_workflow(doc)]
        self.assertIn("S10", rules)

    def test_domain_ordering_violation_is_logic_hole(self):
        """结构全对但 upload_store 不依赖签名 → 域规则抓住逻辑漏洞。"""
        doc = {"workflow": "x", "steps": [
            {"name": "gen_config", "risk": "normal", "service": "a", "action": "g", "depends_on": []},
            {"name": "render_preview", "risk": "normal", "service": "b", "action": "r", "depends_on": ["gen_config"]},
            {"name": "send_screenshot", "risk": "high", "service": "c", "action": "s", "depends_on": ["render_preview"]},
            {"name": "wait_customer_confirm", "risk": "human_wait", "depends_on": ["send_screenshot"]},
            {"name": "android_build", "risk": "normal", "service": "d", "action": "b", "depends_on": ["wait_customer_confirm"]},
            {"name": "ios_sign", "risk": "high", "service": "e", "action": "i", "depends_on": ["android_build"]},
            {"name": "build_submit_materials", "risk": "normal", "service": "f", "action": "m", "depends_on": ["wait_customer_confirm"]},
            {"name": "upload_store", "risk": "high", "service": "g", "action": "u", "depends_on": ["android_build"]},
        ]}
        errs = validate_workflow(doc)
        msgs = [e["msg"] for e in errs if e["rule"] == "D"]
        self.assertTrue(any("ios_sign" in m for m in msgs), errs)
        self.assertTrue(any("build_submit_materials" in m for m in msgs), errs)

    def test_pay_channel_fee_must_be_high_risk(self):
        doc = {"workflow": "x", "steps": [
            {"name": "android_build", "risk": "normal", "service": "d", "action": "b", "depends_on": []},
            {"name": "pay_channel_fee", "risk": "normal", "service": "pay", "action": "fee", "depends_on": ["android_build"]},
        ]}
        errs = validate_workflow(doc)
        self.assertTrue(any(e["rule"] == "D" and "pay_channel_fee" in e["msg"] for e in errs), errs)


if __name__ == "__main__":
    unittest.main()
