"""深潜 Q3 回归：单任务单执行者（flock）与看门狗锁感知。

  1. 锁被活进程持有 → 并发 run 直接 TaskBusyError（CLI exit 3），不产生重复副作用；
  2. 不同任务同 base-dir 互不阻塞（per-task 锁，非全局锁）；
  3. is_locked 与 flock 存活语义一致：持有=占用，释放/死亡=空闲；
  4. 看门狗：活进程持有的 running 任务不得被误重入（skip_requeue_locked）。
"""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from durable_flow.locks import is_locked, task_lock
from durable_flow.watchdog import scan
from helpers import cli, jout

REPO = Path(__file__).resolve().parent.parent


class ConcurrencyTest(unittest.TestCase):
    def test_second_runner_gets_busy_not_duplicate(self):
        with tempfile.TemporaryDirectory() as tmp:
            base, task = Path(tmp), "t-race"
            with task_lock(base, task):            # 模拟活执行者
                r = cli(base, "run", "--task", task)
                self.assertEqual(r.returncode, 3, f"应 exit 3: {r.stdout} {r.stderr}")
                out = json.loads(r.stdout)
                self.assertEqual(out["error"], "task_busy")
            # 锁释放后可正常重入
            st = jout(cli(base, "run", "--task", task))
            self.assertIn(st["status"], ("waiting_human", "waiting_approval"))

    def test_different_tasks_do_not_block_each_other(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            with task_lock(base, "t-a"):
                st = jout(cli(base, "run", "--task", "t-b"))
                self.assertIn(st["status"], ("waiting_human", "waiting_approval"),
                              "t-b 不应被 t-a 的锁阻塞")

    def test_is_locked_reflects_liveness(self):
        with tempfile.TemporaryDirectory() as tmp:
            base, task = Path(tmp), "t-lock"
            self.assertFalse(is_locked(base, task))
            with task_lock(base, task):
                self.assertTrue(is_locked(base, task))
            self.assertFalse(is_locked(base, task))

    def test_watchdog_skips_live_runner(self):
        with tempfile.TemporaryDirectory() as tmp:
            base, task = Path(tmp), "t-wd"
            r = cli(base, "run", "--task", task)   # → 首个等待态
            self.assertIn(jout(r)["status"], ("waiting_human", "waiting_approval"))
            # 人为置 running 并回退 updated，模拟"长步骤执行中、心跳超期"
            import sqlite3
            conn = sqlite3.connect(base / "durable.db")
            conn.execute("UPDATE tasks SET status='running', updated=1.0")
            conn.commit()
            conn.close()
            with task_lock(base, task):            # 活执行者持有锁
                out = scan(base, stale_seconds=0)  # 全部 running 都"超时"
                self.assertEqual(out["requeued"], [], "活进程任务不得重入")
            out2 = scan(base, stale_seconds=0)     # 锁已释放（执行者死亡）
            self.assertIn(task, out2["requeued"], "死执行者任务应重入")


if __name__ == "__main__":
    unittest.main()
