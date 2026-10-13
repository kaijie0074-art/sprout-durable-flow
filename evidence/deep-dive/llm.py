"""深潜 LLM 客户端与提示词构造（生成→验证闭环）。

约定：API key 只从环境变量 GLM_API_KEY 读取，绝不写入任何文件/日志。
调用串行 + 每次 sleep 2（共享订阅配额节流）；HTTP 429 → sleep 60 重试
（至多 3 次）。响应只保留 message.content（不含请求头/鉴权信息）。
"""
import json
import os
import time
import urllib.request

BASE_URL = "https://open.bigmodel.cn/api/coding/paas/v4"
SLEEP_BETWEEN = 2
RETRY_429 = 3


def chat(model: str, messages: list, temperature: float = 0.7,
         max_tokens: int = 4096, timeout: int = 300) -> str:
    key = os.environ.get("GLM_API_KEY", "")
    assert key, "GLM_API_KEY 未设置"
    req = urllib.request.Request(
        BASE_URL + "/chat/completions",
        data=json.dumps({"model": model, "messages": messages,
                         "temperature": temperature, "max_tokens": max_tokens}).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {key}"})
    for attempt in range(RETRY_429 + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                out = json.loads(resp.read().decode("utf-8"))
            time.sleep(SLEEP_BETWEEN)
            return out["choices"][0]["message"]["content"]
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < RETRY_429:
                print(f"  [429] sleep 60 retry {attempt + 1}/{RETRY_429}", flush=True)
                time.sleep(60)
                continue
            raise
    raise RuntimeError("unreachable")


def extract_json(text: str):
    """从模型输出提取 JSON 对象；失败返回 (None, 错误)。"""
    fence = text.strip()
    if fence.startswith("```"):
        fence = fence.split("```", 2)[1] if fence.count("```") >= 2 else fence[3:]
        if fence.startswith("json"):
            fence = fence[4:]
    start, end = fence.find("{"), fence.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None, "输出中找不到 JSON 对象"
    try:
        return json.loads(fence[start:end + 1]), None
    except json.JSONDecodeError as e:
        return None, f"JSON 解析失败: {e}"


SYSTEM = "你是工作流定义生成器。只输出一个 JSON 对象，不要输出任何解释、markdown 代码块或其他文字。"

DSL_SPEC = """工作流 DSL 格式：
{"workflow": "<名称>", "steps": [{"name": "...", "risk": "...", "service": "...", "action": "...", "depends_on": [...]}]}

字段约束：
- name：小写字母/数字/下划线，步骤间唯一
- risk：normal（普通副作用）/ high（高危动作：外发、签名、上传、资金）/ human_wait（等待人工，无外部副作用）
- service 与 action：副作用步骤的幂等探测锚点，必填且不同步骤不得重复；human_wait 步骤不得填写
- depends_on：本步骤依赖的前置步骤名列表；整体依赖图必须无环，且所有步骤都要从某个无依赖的入口步可达"""

DOMAIN_DOC = """业务域：外包团队给客户交付定制 App 的流水线（一个步骤的副作用一旦发生不可收回）。
可用动作：
- gen_config 生成站点配置
- render_preview 渲染预览页
- send_screenshot 把预览截图外发给客户老板确认（高危：外发消息）
- wait_customer_confirm 等客户文字回复确认（人工等待，可能数小时到数天）
- android_build 打包 Android 构建（必须已获得客户确认才开工）
- ios_sign 用稀缺证书环境给 iOS 包签名（高危；必须已有构建）
- build_submit_materials 整理应用商店上架物料
- upload_store 上传到应用商店后台（高危；必须构建、签名、物料三者齐备）
- pay_channel_fee 支付渠道费用（资金类动作；必须已有构建）
- notify_customer_launch 通知客户已上线（必须已完成商店上传）"""

TIERS = {
    "T1": {"k": 5, "steps": "gen_config、render_preview、send_screenshot、wait_customer_confirm、android_build"},
    "T2": {"k": 8, "steps": "gen_config、render_preview、send_screenshot、wait_customer_confirm、android_build、ios_sign、build_submit_materials、upload_store"},
    "T3": {"k": 10, "steps": "gen_config、render_preview、send_screenshot、wait_customer_confirm、android_build、ios_sign、build_submit_materials、upload_store、pay_channel_fee、notify_customer_launch"},
}


def gen_prompt(tier: str) -> str:
    t = TIERS[tier]
    return f"""为上述 App 交付流水线生成工作流定义。

{DSL_SPEC}

{DOMAIN_DOC}

要求：恰好 {t['k']} 个步骤：{t['steps']}。依赖关系必须符合业务描述中的顺序约束（"必须已有 X" 意味着需要依赖 X 的传递闭包）。"""


def repair_prompt(original_prompt: str, output: str, errors: list) -> str:
    errs = "\n".join(f"- [{e['rule']}] {e['msg']}" for e in errors)
    return f"""你之前生成的工作流定义未通过状态机验证。

原始要求：
{original_prompt}

你之前的输出：
{output}

验证器错误：
{errs}

请输出修正后的完整工作流 JSON（同样只输出 JSON，不要任何解释）。"""
