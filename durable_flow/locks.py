"""任务级互斥锁：同一任务同一时刻只允许一个执行进程（POSIX flock）。

为什么不是数据库锁：执行步内含外部 HTTP 副作用，SQLite 写事务横跨网络
调用会长时间持锁阻塞读；flock 在进程死亡（含 SIGKILL）时由内核自动
释放，天然充当看门狗的存活探针——锁被占 = 有活进程在跑，锁空闲 +
状态 running = 上次执行者已死，可安全重入。

不跨 NFS（本地/单机语义）；Windows 无 flock，本库目标环境为 POSIX。
"""
import fcntl
import os
from contextlib import contextmanager
from pathlib import Path


class TaskBusyError(RuntimeError):
    """同一任务已有进程在执行（flock 占用）。"""


def lock_path(base_dir, task_id: str) -> Path:
    safe = "".join(c if (c.isalnum() or c in "-_.") else "_" for c in task_id)
    d = Path(base_dir) / ".locks"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{safe}.lock"


def is_locked(base_dir, task_id: str) -> bool:
    """该任务的执行锁当前是否被某个活进程持有。"""
    p = lock_path(base_dir, task_id)
    if not p.exists():
        return False
    fd = os.open(str(p), os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    except OSError:
        return True
    finally:
        os.close(fd)


@contextmanager
def task_lock(base_dir, task_id: str):
    p = lock_path(base_dir, task_id)
    fd = os.open(str(p), os.O_CREAT | os.O_RDWR, 0o644)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise TaskBusyError(
                f"任务 {task_id} 已有进程在执行（{p} 被占用）；"
                "状态机单任务单执行者，请稍后重试或检查残留进程。")
        yield
    finally:
        os.close(fd)   # 关闭 fd 即释放 flock（含异常/崩溃路径）
