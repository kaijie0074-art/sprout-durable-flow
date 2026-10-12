"""【缺陷基线·现状】命令行入口——与最终态共用命令面（构成回归对比），
但行为是 naive 的：无门、无看门狗、CAS 恒成功、audit 恒空。
"""
import argparse
import json
import sys
from pathlib import Path

from .naive import MEMORY, NaiveRunner


def main(argv=None):
    parser = argparse.ArgumentParser(prog="durable_flow", description="长任务执行器（naive）")
    parser.add_argument("--base-dir", default=".")
    sub = parser.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run")
    r.add_argument("--task", required=True)
    r.add_argument("--project", default="default-project")
    r.add_argument("--crash-at", default=None)

    a = sub.add_parser("approve")
    a.add_argument("--task", required=True)
    a.add_argument("--step", required=True)

    c = sub.add_parser("confirm")
    c.add_argument("--task", required=True)
    c.add_argument("--reply", default="可以了")

    s = sub.add_parser("status")
    s.add_argument("--task", required=True)

    au = sub.add_parser("audit")
    au.add_argument("--task", required=True)

    w = sub.add_parser("watchdog")
    w.add_argument("--stale", type=float, default=3600)

    cs = sub.add_parser("cas")
    cs.add_argument("--project", required=True)
    cs.add_argument("--expected-version", type=int, required=True)
    cs.add_argument("--data", required=True)

    args = parser.parse_args(argv)
    base = Path(args.base_dir)

    if args.cmd == "run":
        out = NaiveRunner(base).run(args.task, project=args.project, crash_at=args.crash_at)
    elif args.cmd == "approve":
        out = {"task_id": args.task, "approved": args.step}   # 现状：没有审批概念
    elif args.cmd == "confirm":
        out = {"task_id": args.task, "confirmed": True}        # 现状：确认不落库
    elif args.cmd == "status":
        mem = base / MEMORY
        if mem.exists():
            text = mem.read_text(encoding="utf-8")
            out = {"task_id": args.task,
                   "status": "done" if "全部完成" in text else "running",
                   "memory": text}
        else:
            out = {"task_id": args.task, "status": "unknown", "memory": ""}
    elif args.cmd == "audit":
        out = {"task_id": args.task, "audits": []}             # 现状：无审计
    elif args.cmd == "watchdog":
        out = {"requeued": [], "alerts": []}                   # 现状：无看门狗
    elif args.cmd == "cas":
        out = {"ok": True, "version": 1}                       # 现状：后写覆盖先写
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
