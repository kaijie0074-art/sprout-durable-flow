"""SQLite 任务存储：任务表 / 检查点 / 幂等去重 / 审计 / CAS 配置。

设计要点（源文档 2.3）：
  - 任务状态只进数据库，禁止把 OpenClaw memory 当状态存储；
  - WAL 模式：任意步 SIGKILL 后已提交事务不丢，恢复一致；
  - configs 表带版本号，写入走 CAS（compare-and-swap），冲突即拒绝。
"""
import json
import sqlite3
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    task_id      TEXT PRIMARY KEY,
    project      TEXT NOT NULL,
    status       TEXT NOT NULL,          -- pending/running/waiting_human/waiting_approval/succeeded/failed
    step_cursor  INTEGER NOT NULL DEFAULT 0,
    current_step TEXT,
    created      REAL NOT NULL,
    updated      REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS checkpoints (
    task_id   TEXT NOT NULL,
    step      TEXT NOT NULL,
    state     TEXT NOT NULL,
    ts        REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS idempotency (
    task_id TEXT NOT NULL,
    step    TEXT NOT NULL,
    key     TEXT NOT NULL,
    PRIMARY KEY (task_id, step, key)
);
CREATE TABLE IF NOT EXISTS audit (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    step    TEXT NOT NULL,
    action  TEXT NOT NULL,               -- APPROVE / EXEC / SKIP / CONFIRM_REPLY / REQUEUE
    detail  TEXT,
    ts      REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS configs (
    project   TEXT NOT NULL,
    version   INTEGER NOT NULL,
    data      TEXT NOT NULL,
    ts        REAL NOT NULL,
    PRIMARY KEY (project, version)
);
"""


class ConfigConflict(Exception):
    """CAS 版本冲突：后写被拒，提示人工合并。"""


class Store:
    def __init__(self, db_path):
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self):
        self.conn.close()

    # ---------- 任务 ----------
    def create_task(self, task_id: str, project: str) -> sqlite3.Row:
        now = time.time()
        self.conn.execute(
            "INSERT OR IGNORE INTO tasks VALUES (?,?,?,0,NULL,?,?)",
            (task_id, project, "pending", now, now))
        self.conn.commit()
        return self.get_task(task_id)

    def get_task(self, task_id: str):
        return self.conn.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()

    def update_task(self, task_id: str, **fields):
        fields["updated"] = time.time()
        keys = ", ".join(f"{k}=?" for k in fields)
        self.conn.execute(f"UPDATE tasks SET {keys} WHERE task_id=?",
                          (*fields.values(), task_id))
        self.conn.commit()

    def stale_running(self, older_than: float) -> list:
        cut = time.time() - older_than
        return self.conn.execute(
            "SELECT * FROM tasks WHERE status='running' AND updated < ?", (cut,)).fetchall()

    def waiting_human(self, older_than: float) -> list:
        cut = time.time() - older_than
        return self.conn.execute(
            "SELECT * FROM tasks WHERE status='waiting_human' AND updated < ?", (cut,)).fetchall()

    # ---------- 检查点 ----------
    def save_checkpoint(self, task_id: str, step: str, state: dict):
        self.conn.execute("INSERT INTO checkpoints VALUES (?,?,?,?)",
                          (task_id, step, json.dumps(state, ensure_ascii=False), time.time()))
        self.conn.commit()

    def done_steps(self, task_id: str) -> list:
        rows = self.conn.execute(
            "SELECT step FROM checkpoints WHERE task_id=? ORDER BY ts", (task_id,)).fetchall()
        return [r["step"] for r in rows]

    # ---------- 幂等 ----------
    def mark_done(self, task_id: str, step: str, key: str):
        self.conn.execute("INSERT OR IGNORE INTO idempotency VALUES (?,?,?)",
                          (task_id, step, key))
        self.conn.commit()

    def is_done(self, task_id: str, step: str, key: str) -> bool:
        return self.conn.execute(
            "SELECT 1 FROM idempotency WHERE task_id=? AND step=? AND key=?",
            (task_id, step, key)).fetchone() is not None

    # ---------- 审计 ----------
    def audit(self, task_id: str, step: str, action: str, detail: str = ""):
        self.conn.execute("INSERT INTO audit(task_id, step, action, detail, ts) VALUES (?,?,?,?,?)",
                          (task_id, step, action, detail, time.time()))
        self.conn.commit()

    def audits(self, task_id: str) -> list:
        rows = self.conn.execute(
            "SELECT task_id, step, action, detail, ts FROM audit WHERE task_id=? ORDER BY id",
            (task_id,)).fetchall()
        return [dict(r) for r in rows]

    def has_audit(self, task_id: str, step: str, action: str) -> bool:
        return self.conn.execute(
            "SELECT 1 FROM audit WHERE task_id=? AND step=? AND action=?",
            (task_id, step, action)).fetchone() is not None

    # ---------- 配置 CAS ----------
    def latest_version(self, project: str) -> int:
        row = self.conn.execute(
            "SELECT MAX(version) AS v FROM configs WHERE project=?", (project,)).fetchone()
        return row["v"] if row["v"] is not None else 0

    def write_config_cas(self, project: str, expected_version: int, data: dict) -> int:
        """带版本号的 CAS 写入：expected_version 必须等于当前最新，否则拒绝。"""
        if self.latest_version(project) != expected_version:
            raise ConfigConflict(
                f"版本冲突：期望基于 v{expected_version}，实际已到 v{self.latest_version(project)}")
        version = expected_version + 1
        self.conn.execute("INSERT INTO configs VALUES (?,?,?,?)",
                          (project, version, json.dumps(data, ensure_ascii=False), time.time()))
        self.conn.commit()
        return version

    def latest_config(self, project: str):
        v = self.latest_version(project)
        if v == 0:
            return None
        row = self.conn.execute(
            "SELECT data FROM configs WHERE project=? AND version=?", (project, v)).fetchone()
        return json.loads(row["data"])
