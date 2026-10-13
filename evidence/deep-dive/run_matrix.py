"""深潜 Q1：崩溃注入矩阵运行器。

用法：python3 evidence/deep-dive/run_matrix.py [--out evidence/deep-dive/data/q1-matrix.json]
逐格（步骤 × 崩溃点）在干净 base 目录注入 SIGKILL，随后驱动恢复
（自动 confirm/approve），以 external_world.jsonl 为真相源判定：
PASS / REPLAY（副作用>1）/ LOSS（succeeded 但副作用=0）/ STUCK。

对每个执行步骤覆盖 6 个原子边界崩溃点；human_wait 步骤覆盖其特有边界
（wait_set：置 waiting_human 后返回前；post_ckpt：确认检查点后）。
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
EVID = REPO / "evidence" / "deep-dive"
SCRATCH = REPO / "evidence" / "deep-dive" / ".scratch"

EXEC_STEPS = ["gen_config", "render_preview", "send_screenshot", "android_build",
              "ios_sign", "build_submit_materials", "upload_store"]
EXEC_POINTS = ["probe", "inflight_pre", "mid_exec", "post_exec", "post_idem",
               "post_audit", "post_ckpt"]
WAIT_STEP = "wait_customer_confirm"
WAIT_POINTS = ["wait_set", "post_ckpt"]
HIGH_RISK = {"send_screenshot", "ios_sign", "upload_store"}
# 7 个有外部副作用的步骤锚点（journal_anchor 同款）
ANCHORS = {
    "gen_config": ("agent", "gen_config"),
    "render_preview": ("renderer", "render_preview"),
    "send_screenshot": ("wecom", "send_message"),
    "android_build": ("builder", "android_build"),
    "ios_sign": ("signer", "sign_ios"),
    "build_submit_materials": ("agent", "materials"),
    "upload_store": ("store", "upload"),
}


def cli(base, *args, crash_point=None, use_crash_at=None):
    env = dict(os.environ)
    env["DURABLE_FLOW_SERVICES_MODULE"] = "crashpoint_services"
    env["PYTHONPATH"] = str(EVID) + os.pathsep + str(REPO) + os.pathsep + str(REPO / "tests") + os.pathsep + env.get("PYTHONPATH", "")
    env["CRASH_BASE"] = str(base)
    if crash_point:
        env["CRASH_POINT"] = crash_point
    else:
        env.pop("CRASH_POINT", None)
    extra = ["--crash-at", use_crash_at] if use_crash_at else []
    return subprocess.run(
        [sys.executable, "-m", "durable_flow.cli", "--base-dir", str(base), *extra, *map(str, args)],
        capture_output=True, text=True, cwd=REPO, env=env)


def world_entries(base):
    p = Path(base) / "logs" / "external_world.jsonl"
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def ckpt_counts(base):
    import sqlite3
    conn = sqlite3.connect(Path(base) / "durable.db")
    rows = conn.execute(
        "SELECT step, COUNT(*) c FROM checkpoints GROUP BY step HAVING c > 1").fetchall()
    conn.close()
    return {r[0]: r[1] for r in rows}


def handle_waiting_approval(base, task, step, crash_point=None) -> str:
    """审批门 / UNCERTAIN 裁决。后者模拟人工核实：查 external_world 该步
    副作用是否已送达——已送达 assume_done（不重发），未送达 resend。"""
    r = cli(base, "audit", "--task", task, crash_point=crash_point)
    audits = json.loads(r.stdout)["audits"]
    if any(a["step"] == step and a["action"] == "UNCERTAIN" for a in audits):
        svc, act = ANCHORS[step]
        delivered = any(e["service"] == svc and e["action"] == act
                        and e["key"] == f"{task}:{step}" for e in world_entries(base))
        resolution = "assume_done" if delivered else "resend"
        cli(base, "approve", "--task", task, "--step", step,
            "--resolution", resolution, crash_point=crash_point)
        return resolution
    cli(base, "approve", "--task", task, "--step", step, crash_point=crash_point)
    return "gate"

def run_cell(step: str, point: str) -> dict:
    base = SCRATCH / f"{step}_{point}"
    if base.exists():
        shutil.rmtree(base)
    base.mkdir(parents=True)
    task = "matrix-task"
    cp = f"{step}:{point}"
    use_crash_at = step if point == "wait_set" else None

    killed = False
    # 第一枪：带崩溃点跑到被 SIGKILL（或穿过等待态后命中）
    for _ in range(30):
        r = cli(base, "run", "--task", task, crash_point=cp, use_crash_at=use_crash_at)
        if r.returncode != 0:
            killed = True
            break
        try:
            st = json.loads(r.stdout)
        except json.JSONDecodeError:
            return {"step": step, "point": point, "verdict": "STUCK",
                    "error": f"bad stdout: {r.stdout[:200]} {r.stderr[:300]}"}
        if st.get("status") == "waiting_human":
            cli(base, "confirm", "--task", task, "--reply", "可以了", crash_point=cp)
        elif st.get("status") == "waiting_approval":
            handle_waiting_approval(base, task, st["current_step"], crash_point=cp)
        else:
            return {"step": step, "point": point, "verdict": "STUCK",
                    "error": f"first-shot unexpected status {st}"}
    if not killed:
        return {"step": step, "point": point, "verdict": "STUCK",
                "error": "crash never triggered"}

    # 恢复：不再注入，驱动到终态
    status, human_touches = None, 0
    for _ in range(30):
        r = cli(base, "run", "--task", task)
        if r.returncode != 0:
            status = f"crash_on_resume:{r.stderr[:200]}"
            break
        st = json.loads(r.stdout)
        status = st.get("status")
        if status == "succeeded":
            break
        if status == "waiting_human":
            cli(base, "confirm", "--task", task, "--reply", "可以了")
            human_touches += 1
        elif status == "waiting_approval":
            human_touches += 1
            handle_waiting_approval(base, task, st["current_step"])
        else:
            break

    world = world_entries(base)
    counts = {}
    for st_name, (svc, act) in ANCHORS.items():
        counts[st_name] = sum(1 for e in world
                              if e["service"] == svc and e["action"] == act
                              and e["key"] == f"matrix-task:{st_name}")
    dup_ckpt = ckpt_counts(base)

    verdict, detail = None, {}
    if status != "succeeded":
        verdict = "STUCK"
        detail["final_status"] = status
    else:
        replays = [k for k, v in counts.items() if v > 1]
        losses = [k for k, v in counts.items() if v == 0]
        if replays:
            verdict = "REPLAY"
            detail["replayed"] = {k: counts[k] for k in replays}
        elif losses:
            verdict = "LOSS"
            detail["lost"] = losses
        else:
            verdict = "PASS"
    if dup_ckpt:
        detail["dup_checkpoints"] = dup_ckpt
    detail["human_touches"] = human_touches
    detail["world_counts"] = counts
    return {"step": step, "point": point, "verdict": verdict, **detail}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(EVID / "data" / "q1-matrix.json"))
    args = ap.parse_args()
    SCRATCH.mkdir(parents=True, exist_ok=True)
    cells = []
    for s in EXEC_STEPS:
        for p in EXEC_POINTS:
            cells.append(run_cell(s, p))
            print(f"[cell] {s}:{p} -> {cells[-1]['verdict']}", flush=True)
    for p in WAIT_POINTS:
        cells.append(run_cell(WAIT_STEP, p))
        print(f"[cell] {WAIT_STEP}:{p} -> {cells[-1]['verdict']}", flush=True)

    summary = {}
    for c in cells:
        summary[c["verdict"]] = summary.get(c["verdict"], 0) + 1
    out = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "cells": cells,
           "summary": summary, "total": len(cells)}
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=1))
    print(json.dumps(summary))
    shutil.rmtree(SCRATCH, ignore_errors=True)


if __name__ == "__main__":
    main()
