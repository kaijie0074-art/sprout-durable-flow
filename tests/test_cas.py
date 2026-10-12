"""验收判据五：并发写——CAS 版本冲突后写被拒（源文档 2.2 场景 5）。"""
import json
import tempfile
import unittest
from pathlib import Path

from helpers import cli


class CasTest(unittest.TestCase):
    def test_cas_write_and_conflict(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            r1 = cli(base, "cas", "--project", "proj-a", "--expected-version", "0",
                     "--data", '{"theme": "blue"}')
            self.assertEqual(json.loads(r1.stdout)["version"], 1)

            # 两个员工基于 v0 各写一版：后写必须被拒
            r2 = cli(base, "cas", "--project", "proj-a", "--expected-version", "0",
                     "--data", '{"theme": "red"}')
            self.assertNotEqual(r2.returncode, 0, "过期版本写入必须失败")
            body = json.loads(r2.stdout)
            self.assertEqual(body["error"], "version_conflict")
            self.assertEqual(body["latest"], 1, "冲突信息应携带当前最新版本")

            # 基于最新版本重写则成功
            r3 = cli(base, "cas", "--project", "proj-a", "--expected-version", "1",
                     "--data", '{"theme": "red"}')
            self.assertEqual(json.loads(r3.stdout)["version"], 2)


if __name__ == "__main__":
    unittest.main()
