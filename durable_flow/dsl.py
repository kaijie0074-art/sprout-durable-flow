"""工作流 DSL 验证器：LLM 生成 → 状态机验证闭环的机器侧裁判。

两层规则：
  结构层 S1–S10：JSON 形状 / 命名 / risk 枚举 / 依赖存在性 / DAG /
    入口存在 / 可达性 / 副作用锚点声明 / 锚点唯一。
  域层 D1–D7：App 交付链的安全与业务顺序约束（外发前有产物、客户
    确认后才打包、签名前有构建、上传前构建+签名+物料齐备、资金类
    高危、上线通知在上传后）。结构合法但违反域规则 = 逻辑漏洞。

validate_workflow 返回结构化错误列表（rule 编号 + 人读信息），既是
回归测试断言对象，也是 LLM 自修复轮的反馈输入。
"""
import re

RISKS = ("normal", "high", "human_wait")
NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")

# 域规则依赖要求：step -> 必须传递依赖的前置步
DOMAIN_REQUIRES = {
    "send_screenshot": ["render_preview", "gen_config"],
    "android_build": ["wait_customer_confirm"],
    "ios_sign": ["android_build"],
    "upload_store": ["android_build", "ios_sign", "build_submit_materials"],
    "pay_channel_fee": ["android_build"],
    "notify_customer_launch": ["upload_store"],
}
DOMAIN_HIGH_RISK = {"pay_channel_fee"}   # 资金类动作恒为高危


def _deps(steps):
    return {s["name"]: list(s.get("depends_on") or []) for s in steps}


def _transitive(steps):
    deps = _deps(steps)
    memo = {}

    def closure(name, seen):
        if name in memo:
            return memo[name]
        out = set()
        for d in deps.get(name, []):
            if d in seen:            # 环在 S6 单独报，这里防御
                continue
            out.add(d)
            out |= closure(d, seen | {d})
        memo[name] = out
        return out

    return {n: closure(n, {n}) for n in deps}


def validate_workflow(doc) -> list:
    """返回错误列表 [{"rule": "S3", "msg": "..."}]；空列表 = 完全合法。"""
    errors = []

    def err(rule, msg):
        errors.append({"rule": rule, "msg": msg})

    if not isinstance(doc, dict):
        return [{"rule": "S1", "msg": "顶层必须是 JSON 对象"}]
    if not isinstance(doc.get("workflow"), str) or not doc["workflow"].strip():
        err("S1", "缺少非空字符串字段 workflow")
    steps = doc.get("steps")
    if not isinstance(steps, list) or not steps:
        err("S2", "steps 必须是非空数组")
        return errors

    names = []
    for i, s in enumerate(steps):
        if not isinstance(s, dict):
            err("S2", f"steps[{i}] 必须是对象")
            continue
        n = s.get("name")
        if not isinstance(n, str) or not NAME_RE.match(n):
            err("S3", f"steps[{i}].name 非法：{n!r}（需匹配 ^[a-z][a-z0-9_]*$）")
        elif n in names:
            err("S3", f"步骤名重复：{n}")
        else:
            names.append(n)
        if s.get("risk") not in RISKS:
            err("S4", f"{n}: risk 必须是 {RISKS} 之一，得到 {s.get('risk')!r}")

    by_name = {s.get("name"): s for s in steps if isinstance(s, dict)}
    for s in steps:
        if not isinstance(s, dict):
            continue
        n = s.get("name")
        for d in s.get("depends_on") or []:
            if d == n:
                err("S5", f"{n}: 依赖自身")
            elif d not in by_name:
                err("S5", f"{n}: 依赖了不存在的步骤 {d!r}")

    # S6 环检测（DFS 变色）
    deps = _deps(steps)
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {n: WHITE for n in by_name}
    cycle_found = set()

    def dfs(n, stack):
        color[n] = GRAY
        for d in deps.get(n, []):
            if d not in by_name:
                continue
            if color[d] == GRAY:
                cycle_found.add(d)
            elif color[d] == WHITE:
                dfs(d, stack)
        color[n] = BLACK

    for n in by_name:
        if color[n] == WHITE:
            dfs(n, [])
    if cycle_found:
        err("S6", f"依赖图存在环，涉及：{sorted(cycle_found)}")

    entries = [n for n, ds in deps.items() if not ds]
    if not entries:
        err("S7", "没有入口步骤（所有步骤都有依赖）")

    trans = _transitive(steps) if not cycle_found else {}
    if entries:
        # n 可达 = 某入口在 n 的祖先闭包中（沿依赖边可从入口走到 n）
        reachable = {n for n, anc in trans.items() if set(entries) & anc} | set(entries)
        for n in by_name:
            if n not in reachable:
                err("S8", f"步骤 {n} 从任何入口都不可达（悬空分支）")

    anchors = {}
    for s in steps:
        if not isinstance(s, dict):
            continue
        n, risk = s.get("name"), s.get("risk")
        svc, act = s.get("service"), s.get("action")
        if risk == "human_wait":
            if svc or act:
                err("S9", f"{n}: human_wait 步骤无副作用，不得声明 service/action")
        else:
            if not isinstance(svc, str) or not svc or not isinstance(act, str) or not act:
                err("S9", f"{n}: 副作用步骤必须声明非空 service 与 action（幂等探测锚点）")
            else:
                key = (svc, act)
                if key in anchors:
                    err("S10", f"{n}: 副作用锚点 ({svc},{act}) 与 {anchors[key]} 冲突")
                anchors[key] = n

    # —— 域层 ——
    for n, required in DOMAIN_REQUIRES.items():
        if n not in by_name:
            continue
        for r in required:
            if r not in trans.get(n, set()):
                err("D", f"{n}: 缺少对 {r} 的（传递）依赖——域顺序约束被违反")
    for n in DOMAIN_HIGH_RISK:
        if n in by_name and by_name[n].get("risk") != "high":
            err("D", f"{n}: 资金类动作必须 risk=high")
    return errors


def to_steps(doc) -> list:
    """合法 DSL 编译为引擎 STEPS 同构列表（name/risk/service/action）。"""
    return [{"name": s["name"], "risk": s["risk"],
             "service": s.get("service"), "action": s.get("action")}
            for s in doc["steps"]]
