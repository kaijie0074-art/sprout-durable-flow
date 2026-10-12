"""看门狗：扫描中断/超时任务，重新入队并出事件。

无人值守窗口（夜间/周末）的基本可用性：跑挂的任务自动重入，
卡住的人工等待超时告警推送到 IM（本模拟写 events.jsonl）。
"""
import json
import time
from pathlib import Path

from .store import Store


def _events_path(base_dir) -> Path:
    p = Path(base_dir) / "logs" / "events.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _emit(base_dir, etype: str, payload: dict):
    with _events_path(base_dir).open("a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": round(time.time(), 3), "type": etype, **payload},
                           ensure_ascii=False) + "\n")


def scan(base_dir, stale_seconds: float = 3600) -> dict:
    store = Store(Path(base_dir) / "durable.db")
    requeued, alerts = [], []
    for task in store.stale_running(stale_seconds):
        store.update_task(task["task_id"], status="pending")
        store.audit(task["task_id"], "watchdog", "REQUEUE", "running 超时，重新入队")
        _emit(base_dir, "requeue", {"task_id": task["task_id"]})
        requeued.append(task["task_id"])
    for task in store.waiting_human(stale_seconds):
        _emit(base_dir, "alert_still_waiting",
              {"task_id": task["task_id"], "step": task["current_step"]})
        alerts.append(task["task_id"])
    # R2-1（评审修复）：审批悬置同样告警——但绝不自动重入，
    # 审批门不能被看门狗绕过（高危动作的放行只能来自人）。
    approval_alerts = []
    for task in store.waiting_approval(stale_seconds):
        _emit(base_dir, "alert_approval_pending",
              {"task_id": task["task_id"], "step": task["current_step"]})
        approval_alerts.append(task["task_id"])
    return {"requeued": requeued, "alerts": alerts, "approval_alerts": approval_alerts}
