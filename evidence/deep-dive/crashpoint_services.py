"""深潜 Q1 崩溃注入矩阵：两阶段副作用服务 + 细粒度崩溃点补丁。

通过 DURABLE_FLOW_SERVICES_MODULE 注入 CLI 进程。每个执行步骤被改造成
两阶段：①副作用写入 external_world.jsonl（外部世界真相源，模拟"HTTP
请求已达服务器"）②journal 落账（幂等确认）。两阶段之间即"恰好一次"
窗口。CRASH_POINT 控制崩溃位置：

  {step}:probe         进入 run_step 时（派发标记未落，副作用未发生）
  {step}:inflight_pre  #inflight 已落账、外部效果未发出（人工裁决 resend 窗口）
  {step}:mid_exec      副作用已写入外部世界、journal 确认前（恰好一次窗口）
  {step}:post_exec     journal 确认后、mark_done 前（等价内置 --crash-at）
  {step}:post_idem     mark_done 落库后、audit 前
  {step}:post_audit    EXEC 审计后、checkpoint 前
  {step}:post_ckpt     checkpoint 写入后、游标推进前

CRASH_BASE：external_world.jsonl 所在 base 目录（须与 --base-dir 一致）。
"""
import json
import os
import signal
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import durable_flow.engine as engine_mod
import durable_flow.steps as steps_mod
import durable_flow.store as store_mod
from durable_flow.services import Journal
from tests.local_services import LocalRenderer, LocalSigner, LocalStoreUpload, LocalWeCom

_BASE = Path(os.environ.get("CRASH_BASE", "."))
_WORLD = _BASE / "logs" / "external_world.jsonl"


def _crash_if(point: str):
    if os.environ.get("CRASH_POINT", "") == point:
        os.kill(os.getpid(), signal.SIGKILL)


def _world_append(service, action, key):
    _WORLD.parent.mkdir(parents=True, exist_ok=True)
    with _WORLD.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": round(time.time(), 3), "service": service,
                            "action": action, "key": key}, ensure_ascii=False) + "\n")


def _barrier(name):
    """并发实验同步栅栏：BARRIER_STEP 步在执行前等 BARRIER_FILE 出现，
    保证两进程都完成"探测→未见"后再放行，形成确定性的 probe-probe 重叠。"""
    bs, bf = os.environ.get("BARRIER_STEP", ""), os.environ.get("BARRIER_FILE", "")
    if bs and bf and name == bs:
        while not Path(bf).exists():
            time.sleep(0.02)


def _run_step_two_phase(name, svc, key, base_dir):
    _crash_if(f"{name}:probe")
    _barrier(name)
    service, action = steps_mod.journal_anchor(name)
    if service is None:
        return  # 无副作用步（human_wait 由引擎处理，不会进 run_step）
    svc["journal"].mark_inflight(service, action, key)   # 派发边界 WAL（与真实适配器一致）
    _crash_if(f"{name}:inflight_pre")                    # 派发标记已落、效果未发出
    _world_append(service, action, key)                  # ① 外部副作用发生
    _crash_if(f"{name}:mid_exec")                        # 窗口：效果已发生，确认未落账
    svc["journal"].append(service, action, key)          # ② 幂等确认
    _crash_if(f"{name}:post_exec")

_orig_mark_done = store_mod.Store.mark_done
_orig_audit = store_mod.Store.audit
_orig_save_ckpt = store_mod.Store.save_checkpoint

def _patched_mark_done(self, task_id, step, key):
    _orig_mark_done(self, task_id, step, key)
    _crash_if(f"{step}:post_idem")


def _patched_audit(self, task_id, step, action, detail=""):
    _orig_audit(self, task_id, step, action, detail)
    if action == "EXEC":
        _crash_if(f"{step}:post_audit")


def _patched_save_ckpt(self, task_id, step, state):
    _orig_save_ckpt(self, task_id, step, state)
    _crash_if(f"{step}:post_ckpt")


steps_mod.run_step = _run_step_two_phase
engine_mod.run_step = _run_step_two_phase
store_mod.Store.mark_done = _patched_mark_done
store_mod.Store.audit = _patched_audit
store_mod.Store.save_checkpoint = _patched_save_ckpt


def make_services(base_dir):
    j = Journal(Path(base_dir) / "logs")
    return {
        "journal": j,
        "wecom": LocalWeCom(j),
        "renderer": LocalRenderer(j, base_dir),
        "signer": LocalSigner(j, base_dir),
        "store": LocalStoreUpload(j),
    }
