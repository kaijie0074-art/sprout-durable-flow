"""深潜 Q5/Q6/Q7：LLM 生成工作流 DSL → 状态机验证闭环实验。

阶段（--phase）：
  gen        Q5  三复杂度梯度 × N 样本，结构/域合法性通过率
  repair     Q6  对 gen 阶段的不合法样本回喂验证错误，测自修复收敛
  determin   Q7a 同提示词重复 k 次，测输出确定性与合法率方差
  ceiling    Q7b 对域规则失败样本换强模型重试，测模型上限差距

数据落 evidence/deep-dive/data/：q5-gen.jsonl / q6-repair.jsonl /
q7-determin.jsonl / q7-ceiling.jsonl。环境变量 GLM_API_KEY 提供凭证。
"""
import argparse
import json
import sys
import time
from pathlib import Path

EVID = Path(__file__).resolve().parent
sys.path.insert(0, str(EVID))
sys.path.insert(0, str(EVID.parent.parent))

from durable_flow.dsl import validate_workflow  # noqa: E402
import llm  # noqa: E402

DATA = EVID / "data"
FAST, STRONG = "glm-4-flash", "glm-5.3-flash"
N_PER_TIER = 8
K_REPEATS = 5


def classify(doc):
    """返回 (parse_ok, struct_ok, domain_ok, errors)。"""
    if doc is None:
        return False, False, False, [{"rule": "JSON", "msg": "解析失败"}]
    errs = validate_workflow(doc)
    rules = {e["rule"] for e in errs}
    struct_ok = not (rules & {"S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8", "S9", "S10", "JSON"})
    domain_ok = "D" not in rules
    return True, struct_ok, domain_ok, errs


def append(path: Path, rec: dict):
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def phase_gen(model=FAST):
    out_path = DATA / "q5-gen.jsonl"
    out_path.unlink(missing_ok=True)
    stats = {}
    for tier in ("T1", "T2", "T3"):
        s = {"n": N_PER_TIER, "parse_ok": 0, "struct_ok": 0, "domain_ok": 0, "full_valid": 0}
        for i in range(N_PER_TIER):
            prompt = llm.gen_prompt(tier)
            raw = llm.chat(model, [{"role": "system", "content": llm.SYSTEM},
                                   {"role": "user", "content": prompt}])
            doc, perr = llm.extract_json(raw)
            parse_ok, struct_ok, domain_ok, errs = classify(doc if perr is None else None)
            s["parse_ok"] += parse_ok
            s["struct_ok"] += struct_ok
            s["domain_ok"] += domain_ok
            s["full_valid"] += struct_ok and domain_ok
            append(out_path, {"tier": tier, "i": i, "model": model, "raw": raw,
                              "doc": doc, "errors": errs,
                              "parse_ok": parse_ok, "struct_ok": struct_ok,
                              "domain_ok": domain_ok})
            print(f"  [{tier}#{i}] parse={parse_ok} struct={struct_ok} domain={domain_ok}", flush=True)
        stats[tier] = s
    print(json.dumps(stats, ensure_ascii=False))


def phase_repair(model=FAST):
    src = DATA / "q5-gen.jsonl"
    out_path = DATA / "q6-repair.jsonl"
    out_path.unlink(missing_ok=True)
    recs = [json.loads(l) for l in src.read_text(encoding="utf-8").splitlines() if l.strip()]
    invalid = [r for r in recs if not (r["struct_ok"] and r["domain_ok"])]
    stats = {"invalid": len(invalid), "repaired_full": 0, "reduced_errors": 0, "unchanged_or_worse": 0}
    for r in invalid:
        prompt = llm.gen_prompt(r["tier"])
        raw = llm.chat(model, [{"role": "system", "content": llm.SYSTEM},
                               {"role": "user", "content": llm.repair_prompt(prompt, r["raw"], r["errors"])}])
        doc, _ = llm.extract_json(raw)
        parse_ok, struct_ok, domain_ok, errs2 = classify(doc if doc is not None else None)
        fixed = struct_ok and domain_ok
        stats["repaired_full"] += fixed
        if len(errs2) < len(r["errors"]):
            stats["reduced_errors"] += 1
        elif not fixed:
            stats["unchanged_or_worse"] += 1
        append(out_path, {"tier": r["tier"], "i": r["i"], "model": model,
                          "before_errors": r["errors"], "after_raw": raw,
                          "after_errors": errs2, "repaired_full": fixed})
        print(f"  [repair {r['tier']}#{r['i']}] fixed={fixed} errs {len(r['errors'])}->{len(errs2)}", flush=True)
    print(json.dumps(stats, ensure_ascii=False))


def phase_determin(model=FAST):
    out_path = DATA / "q7-determin.jsonl"
    out_path.unlink(missing_ok=True)
    outputs, valid_flags = [], []
    prompt = llm.gen_prompt("T2")
    for i in range(K_REPEATS):
        raw = llm.chat(model, [{"role": "system", "content": llm.SYSTEM},
                               {"role": "user", "content": prompt}])
        doc, _ = llm.extract_json(raw)
        parse_ok, struct_ok, domain_ok, errs = classify(doc if doc is not None else None)
        outputs.append(raw)
        valid_flags.append(struct_ok and domain_ok)
        append(out_path, {"i": i, "model": model, "raw": raw, "valid": struct_ok and domain_ok,
                          "errors": errs})
        print(f"  [det #{i}] valid={struct_ok and domain_ok}", flush=True)
    uniq = len(set(outputs))
    print(json.dumps({"identical_outputs": uniq == 1, "unique_outputs": uniq,
                      "valid_flags": valid_flags}, ensure_ascii=False))


def phase_ceiling(model=STRONG):
    """强模型对 T3（最难档）同题重试，对照弱模型的域失败模式。"""
    src = DATA / "q5-gen.jsonl"
    out_path = DATA / "q7-ceiling.jsonl"
    out_path.unlink(missing_ok=True)
    recs = [json.loads(l) for l in src.read_text(encoding="utf-8").splitlines() if l.strip()]
    t3 = [r for r in recs if r["tier"] == "T3"]
    stats = {"n": len(t3), "full_valid": 0}
    for r in t3:
        prompt = llm.gen_prompt("T3")
        raw = llm.chat(model, [{"role": "system", "content": llm.SYSTEM},
                               {"role": "user", "content": prompt}], timeout=300)
        doc, _ = llm.extract_json(raw)
        parse_ok, struct_ok, domain_ok, errs = classify(doc if doc is not None else None)
        fixed = struct_ok and domain_ok
        stats["full_valid"] += fixed
        append(out_path, {"i": r["i"], "model": model, "raw": raw, "valid": fixed, "errors": errs,
                          "weak_model_was_valid": r["struct_ok"] and r["domain_ok"]})
        print(f"  [strong T3#{r['i']}] valid={fixed}", flush=True)
    print(json.dumps(stats, ensure_ascii=False))



HARDEN_EXTRA = """
再次强调字段联合约束（违反即非法）：
- risk 为 "human_wait" 的步骤：对象只允许 name、risk、depends_on 三个字段，不得出现 service、action
- 其他 risk 的步骤：必须同时有 service 与 action（非空字符串），且 (service, action) 组合全局唯一
反例（非法）：{"name": "wait_customer_confirm", "risk": "human_wait", "service": "x", "action": "y", "depends_on": []}
正例（合法）：{"name": "wait_customer_confirm", "risk": "human_wait", "depends_on": ["send_screenshot"]}
"""


def phase_harden(model=FAST):
    """Q8：对 4-flash 的顽固 S9 违规做提示词工程对照（显式反例 + 字段联合规则）。"""
    out_path = DATA / "q8-harden.jsonl"
    out_path.unlink(missing_ok=True)
    n, valid = 6, 0
    for i in range(n):
        prompt = llm.gen_prompt("T2") + HARDEN_EXTRA
        raw = llm.chat(model, [{"role": "system", "content": llm.SYSTEM},
                               {"role": "user", "content": prompt}])
        doc, _ = llm.extract_json(raw)
        parse_ok, struct_ok, domain_ok, errs = classify(doc if doc is not None else None)
        ok = struct_ok and domain_ok
        valid += ok
        append(out_path, {"i": i, "model": model, "raw": raw, "valid": ok, "errors": errs})
        print(f"  [harden #{i}] valid={ok} errs={len(errs)}", flush=True)
    print(json.dumps({"n": n, "full_valid": valid}, ensure_ascii=False))

def phase_harden2(model=FAST):
    """Q8b：harden 提示词 + 验证错误回喂的组合闭环（弱模型能否被工程闭环救回）。"""
    src = DATA / "q8-harden.jsonl"
    out_path = DATA / "q8-harden2.jsonl"
    out_path.unlink(missing_ok=True)
    recs = [json.loads(l) for l in src.read_text(encoding="utf-8").splitlines() if l.strip()]
    invalid = [r for r in recs if not r["valid"]]
    stats = {"invalid": len(invalid), "repaired_full": 0}
    for r in invalid:
        prompt = llm.gen_prompt("T2") + HARDEN_EXTRA
        raw = llm.chat(model, [{"role": "system", "content": llm.SYSTEM},
                               {"role": "user", "content": llm.repair_prompt(prompt, r["raw"], r["errors"])}])
        doc, _ = llm.extract_json(raw)
        parse_ok, struct_ok, domain_ok, errs = classify(doc if doc is not None else None)
        fixed = struct_ok and domain_ok
        stats["repaired_full"] += fixed
        append(out_path, {"i": r["i"], "model": model, "after_raw": raw,
                          "after_errors": errs, "repaired_full": fixed})
        print(f"  [harden2 #{r['i']}] fixed={fixed} errs={len(errs)}", flush=True)
    print(json.dumps(stats, ensure_ascii=False))




if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", required=True,
                    choices=["gen", "repair", "determin", "ceiling", "harden", "harden2"])
    ap.add_argument("--model", default=None)
    args = ap.parse_args()
    DATA.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    {"gen": phase_gen, "repair": phase_repair, "determin": phase_determin,
     "ceiling": phase_ceiling, "harden": phase_harden, "harden2": phase_harden2}[args.phase](
        model=args.model or (STRONG if args.phase == "ceiling" else FAST))
    print(f"phase={args.phase} elapsed={time.time() - t0:.0f}s", flush=True)
