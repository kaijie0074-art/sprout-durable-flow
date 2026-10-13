"""深潜 Q4：看门狗重入误报率。

场景：任务合法长跑（如签名机 5 分钟），updated 停留在开跑时刻；stale_seconds
小于步骤时长即判"超时"。修复前语义 = 只看时间戳 → 活任务被 requeue →
与新 run 形成 Q3 的双执行者副作用重复。修复后 = 时间戳超时且锁空闲才
requeue（flock 存活探针）。

本实验在同一进程内做配对对照：
  pre-fix 仿真：watchdog.is_locked 打桩为恒 False（即修复前无锁感知）
  post-fix    ：真实 is_locked
每侧 N 个任务：live（持锁长跑）/ dead（无锁残留）。指标：
  FP = 活任务被 requeue 的比例；TP = 死任务被 requeue 的比例。
"""
import json
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))

import durable_flow.watchdog as wd
from durable_flow.locks import task_lock

N = 10


def make_running_task(base, task_id):
    """建库并造一个 status=running、updated 已超期的任务（模拟长跑）。"""
    from durable_flow.store import Store
    store = Store(Path(base) / "durable.db")
    store.create_task(task_id, "p")
    store.update_task(task_id, status="running")
    conn = sqlite3.connect(Path(base) / "durable.db")
    conn.execute("UPDATE tasks SET updated=1.0")   # 时间戳彻底超期
    conn.commit()
    conn.close()
    store.close()


def run_side(base, live: bool, patch_locked: bool) -> dict:
    ids = [f"t-{'live' if live else 'dead'}-{i}" for i in range(N)]
    for i in ids:
        make_running_task(base, i)
    held = [task_lock(base, i) for i in ids] if live else []
    for h in held:
        h.__enter__()
    try:
        if patch_locked:
            orig = wd.is_locked
            wd.is_locked = lambda b, t: False   # 修复前：无锁感知
        try:
            out = wd.scan(base, stale_seconds=0)
        finally:
            if patch_locked:
                wd.is_locked = orig
    finally:
        for h in held:
            h.__exit__(None, None, None)
    return out


def main():
    result = {}
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        pre_live = run_side(base, live=True, patch_locked=True)
        # pre-fix：活任务（持锁）被 requeue → 误报
        result["pre_fix_live_requeued"] = len(pre_live["requeued"])
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        post_live = run_side(base, live=True, patch_locked=False)
        skipped = sum(1 for l in (Path(base) / "logs" / "events.jsonl").read_text().splitlines()
                      if '"skip_requeue_locked"' in l)
        result["post_fix_live_requeued"] = len(post_live["requeued"])
        result["post_fix_live_skipped_events"] = skipped
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        post_dead = run_side(base, live=False, patch_locked=False)
        result["post_fix_dead_requeued"] = len(post_dead["requeued"])
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        pre_dead = run_side(base, live=False, patch_locked=True)
        result["pre_fix_dead_requeued"] = len(pre_dead["requeued"])

    result.update({
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "fp_rate_pre_fix": result["pre_fix_live_requeued"] / N,
        "fp_rate_post_fix": result["post_fix_live_requeued"] / N,
        "tp_rate_post_fix": result["post_fix_dead_requeued"] / N,
    })
    out_path = REPO / "evidence" / "deep-dive" / "data" / "q4-watchdog-fp.json"
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=1))
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
