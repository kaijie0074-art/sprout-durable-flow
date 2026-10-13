"""测试辅助：以子进程方式驱动 CLI（baseline 与最终态共用同一命令面，
测试断言的接口完全一致，从而构成回归对比）。

子进程注入 DURABLE_FLOW_SERVICES_MODULE=local_services（tests/ 内的本地
执行后端），使崩溃恢复/幂等回归不依赖真实外发服务；产品代码默认真实
HTTP 适配器（env-gated）。"""
import json
import os
import subprocess
import sys
from pathlib import Path
REPO = Path(__file__).resolve().parent.parent


HIGH_RISK = ("send_screenshot", "ios_sign", "upload_store")


def cli(base, *args):
    env = dict(os.environ)
    env["DURABLE_FLOW_SERVICES_MODULE"] = "local_services"
    env["PYTHONPATH"] = str(Path(__file__).resolve().parent) + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(
        [sys.executable, "-m", "durable_flow.cli", "--base-dir", str(base), *map(str, args)],
        capture_output=True, text=True, cwd=REPO, env=env)


def jout(r) -> dict:
    return json.loads(r.stdout)


def journal_entries(base) -> list:
    p = Path(base) / "logs" / "journal.jsonl"
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def events(base) -> list:
    p = Path(base) / "logs" / "events.jsonl"
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def approve_all(base, task, steps=HIGH_RISK):
    for s in steps:
        cli(base, "approve", "--task", task, "--step", s)

def drive(base, task, crash_at=None) -> dict:
    """带故障注入驱动：穿过 waiting_human/waiting_approval，直到进程被杀或任务完成。"""
    for _ in range(20):
        extra = ["--crash-at", crash_at] if crash_at else []
        r = cli(base, "run", "--task", task, *extra)
        if r.returncode != 0:
            return {"killed": True}
        st = jout(r)
        if st.get("status") == "succeeded":
            return {"killed": False, "status": st}
        if st.get("status") == "waiting_human":
            cli(base, "confirm", "--task", task, "--reply", "可以了")
        elif st.get("status") == "waiting_approval":
            cli(base, "approve", "--task", task, "--step", st["current_step"])
        else:
            raise AssertionError(f"意外状态 {st} stderr={r.stderr}")
    raise AssertionError("20 轮仍未结束")


def drive_to_success(base, task) -> dict:
    """反复 run，自动处理 waiting_human（confirm）与 waiting_approval（approve）。"""
    for _ in range(20):
        r = cli(base, "run", "--task", task)
        st = jout(r)
        if st.get("status") == "succeeded":
            return st
        if st.get("status") == "waiting_human":
            cli(base, "confirm", "--task", task, "--reply", "可以了")
        elif st.get("status") == "waiting_approval":
            cli(base, "approve", "--task", task, "--step", st["current_step"])
        else:
            raise AssertionError(f"意外状态 {st} stderr={r.stderr}")
    raise AssertionError("20 轮仍未到 succeeded")
