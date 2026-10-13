"""深潜 Q3：并发实例竞争——两个进程同 base-dir 同任务同时 run。

确定性重叠构造：两进程在 BARRIER_STEP=gen_config 处等待栅栏文件，即
两进程都已完成"前置探测→未见"后才放行执行，暴露 probe-probe 竞态。
外部世界真相源 external_world.jsonl 判定副作用是否重复。

用法：python3 evidence/deep-dive/run_race.py [--out .../q3-race.json]
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
SCRATCH = EVID / ".scratch-race"

ANCHORS = {
    "gen_config": ("agent", "gen_config"),
    "render_preview": ("renderer", "render_preview"),
    "send_screenshot": ("wecom", "send_message"),
    "android_build": ("builder", "android_build"),
    "ios_sign": ("signer", "sign_ios"),
    "build_submit_materials": ("agent", "materials"),
    "upload_store": ("store", "upload"),
}


def cli_env(base, extra_env=None):
    env = dict(os.environ)
    env["DURABLE_FLOW_SERVICES_MODULE"] = "crashpoint_services"
    env["PYTHONPATH"] = (str(EVID) + os.pathsep + str(REPO) + os.pathsep +
                         str(REPO / "tests") + os.pathsep + env.get("PYTHONPATH", ""))
    env["CRASH_BASE"] = str(base)
    env.pop("CRASH_POINT", None)
    if extra_env:
        env.update(extra_env)
    return env


def spawn(base, task, barrier_file, label):
    env = cli_env(base, {"BARRIER_STEP": "gen_config", "BARRIER_FILE": str(barrier_file)})
    return subprocess.Popen(
        [sys.executable, "-m", "durable_flow.cli", "--base-dir", str(base),
         "run", "--task", task],
        capture_output=False, text=True, cwd=REPO, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def world_counts(base, task):
    p = Path(base) / "logs" / "external_world.jsonl"
    out = {}
    if not p.exists():
        return out
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        e = json.loads(line)
        for step, (svc, act) in ANCHORS.items():
            if e["service"] == svc and e["action"] == act and e["key"] == f"{task}:{step}":
                out[step] = out.get(step, 0) + 1
    return out


def spawn(base, task, barrier_file):
    env = cli_env(base, {"BARRIER_STEP": "gen_config", "BARRIER_FILE": str(barrier_file)})
    return subprocess.Popen(
        [sys.executable, "-m", "durable_flow.cli", "--base-dir", str(base),
         "run", "--task", task],
        text=True, cwd=REPO, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def run_race() -> dict:
    if SCRATCH.exists():
        shutil.rmtree(SCRATCH)
    SCRATCH.mkdir(parents=True)
    base = SCRATCH / "base"
    base.mkdir()
    task = "race-task"
    barrier = SCRATCH / "go"

    # 预放行高危步 + 预落客户确认，使两进程可一口气跑完全部副作用步
    pre = cli_env(base)
    for step in ("send_screenshot", "ios_sign", "upload_store"):
        subprocess.run([sys.executable, "-m", "durable_flow.cli", "--base-dir", str(base),
                        "approve", "--task", task, "--step", step],
                       capture_output=True, text=True, cwd=REPO, env=pre)
    subprocess.run([sys.executable, "-m", "durable_flow.cli", "--base-dir", str(base),
                    "confirm", "--task", task, "--reply", "可以了"],
                   capture_output=True, text=True, cwd=REPO, env=pre)

    p1 = spawn(base, task, barrier)
    p2 = spawn(base, task, barrier)
    time.sleep(1.5)          # 两进程均抵达 gen_config 栅栏
    barrier.touch()          # 放行 → probe-probe 重叠
    o1, e1 = p1.communicate(timeout=60)
    o2, e2 = p2.communicate(timeout=60)

    counts = world_counts(base, task)
    status = subprocess.run(
        [sys.executable, "-m", "durable_flow.cli", "--base-dir", str(base),
         "status", "--task", task], capture_output=True, text=True,
        cwd=REPO, env=cli_env(base)).stdout

    dup = {k: v for k, v in counts.items() if v > 1}
    return {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "returncodes": [p1.returncode, p2.returncode],
        "world_counts": counts,
        "duplicated_steps": dup,
        "verdict": "RACE_DUPLICATED" if dup else "SERIALIZED",
        "locked_errors": sum(s in (e1 + e2) for s in ["database is locked", "TaskBusy"]),
        "final_status": json.loads(status)["status"],
        "stderr_p1": e1[-300:], "stderr_p2": e2[-300:],
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(EVID / "data" / "q3-race.json"))
    args = ap.parse_args()
    result = run_race()
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=1))
    print(json.dumps({k: result[k] for k in ("verdict", "world_counts",
                                             "duplicated_steps", "returncodes") if k in result},
                     ensure_ascii=False))
    shutil.rmtree(SCRATCH, ignore_errors=True)
