"""命令行入口（baseline 与最终态共用同一命令面，构成回归对比）：

  run      --task T [--project P] [--crash-at STEP]   运行/恢复任务
  approve  --task T --step ios_sign                    高危动作放行
  confirm  --task T --reply "可以了"                    客户确认落库
  status   --task T                                    任务状态（JSON）
  audit    --task T                                    审计记录（JSON）
  watchdog [--stale N]                                 扫描超时任务重入+告警
  cas      --project P --expected-version N --data JSON CAS 写入项目配置
"""
import argparse
import json
import sys
from pathlib import Path

from .engine import Engine
from .store import ConfigConflict
from .watchdog import scan


def main(argv=None):
    parser = argparse.ArgumentParser(prog="durable_flow", description="长任务状态机执行器")
    parser.add_argument("--base-dir", default=".", help="工作目录（db/日志/产物）")
    sub = parser.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="运行/恢复任务（含 --crash-at 故障注入）")
    r.add_argument("--task", required=True)
    r.add_argument("--project", default="default-project")
    r.add_argument("--crash-at", default=None, help="在该步 execute 后、confirm 前 SIGKILL 自身")

    a = sub.add_parser("approve", help="高危动作放行")
    a.add_argument("--task", required=True)
    a.add_argument("--step", required=True)

    c = sub.add_parser("confirm", help="客户确认（凭任务 ID，与原会话无关）")
    c.add_argument("--task", required=True)
    c.add_argument("--reply", default="可以了")

    s = sub.add_parser("status", help="任务状态")
    s.add_argument("--task", required=True)

    au = sub.add_parser("audit", help="审计记录")
    au.add_argument("--task", required=True)

    w = sub.add_parser("watchdog", help="扫描超时/中断任务")
    w.add_argument("--stale", type=float, default=3600)

    cs = sub.add_parser("cas", help="带版本号的 CAS 配置写入")
    cs.add_argument("--project", required=True)
    cs.add_argument("--expected-version", type=int, required=True)
    cs.add_argument("--data", required=True, help="JSON 字符串")

    args = parser.parse_args(argv)
    base = Path(args.base_dir)
    eng = Engine(base)

    if args.cmd == "run":
        out = eng.run(args.task, project=args.project, crash_at=args.crash_at)
    elif args.cmd == "approve":
        out = eng.approve(args.task, args.step)
    elif args.cmd == "confirm":
        out = eng.confirm_customer(args.task, args.reply)
    elif args.cmd == "status":
        task = eng.store.get_task(args.task)
        out = dict(task) if task else {"error": "not_found"}
    elif args.cmd == "audit":
        out = {"task_id": args.task, "audits": eng.store.audits(args.task)}
    elif args.cmd == "watchdog":
        out = scan(base, stale_seconds=args.stale)
    elif args.cmd == "cas":
        try:
            version = eng.store.write_config_cas(
                args.project, args.expected_version, json.loads(args.data))
            out = {"ok": True, "version": version}
        except ConfigConflict as e:
            print(json.dumps({"ok": False, "error": "version_conflict", "message": str(e),
                              "latest": eng.store.latest_version(args.project)},
                             ensure_ascii=False))
            sys.exit(1)
    print(json.dumps(out, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
