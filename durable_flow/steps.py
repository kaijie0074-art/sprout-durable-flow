"""流水线步骤定义（源文档 2.1 的八步交付链）与三段式钩子。

每个步骤声明：risk 等级（normal / high / human_wait）与幂等探测锚点
（service+action+key）。高危四类中的三类出现在本流水线：外发消息、
签名、上传商店；pay_channel_fee 作为第四类风险等级保留定义（源文档
2.3 审批门四类动作），本流水线未排入。
"""
from pathlib import Path

STEPS = [
    {"name": "gen_config",             "risk": "normal"},
    {"name": "render_preview",         "risk": "normal"},
    {"name": "send_screenshot",        "risk": "high"},        # 外发：客户消息
    {"name": "wait_customer_confirm",  "risk": "human_wait"},  # 等客户，数小时~数天
    {"name": "android_build",          "risk": "normal"},
    {"name": "ios_sign",               "risk": "high"},        # 签名：稀缺证书环境
    {"name": "build_submit_materials", "risk": "normal"},
    {"name": "upload_store",           "risk": "high"},        # 商店后台上传
]

STEP_NAMES = [s["name"] for s in STEPS]
HIGH_RISK_STEPS = [s["name"] for s in STEPS if s["risk"] == "high"]


def _artifacts(base_dir) -> Path:
    d = Path(base_dir) / "artifacts"
    d.mkdir(parents=True, exist_ok=True)
    return d


def run_step(name: str, svc: dict, key: str, base_dir):
    """执行步骤副作用（幂等探测由引擎在调用前完成）。"""
    if name == "gen_config":
        (_artifacts(base_dir) / "config.json").write_text(
            '{"siteName": "客户视频站", "templateId": "template_video"}', encoding="utf-8")
        svc["journal"].append("agent", "gen_config", key)
    elif name == "render_preview":
        svc["renderer"].render(key, "<html><body>预览页</body></html>")
    elif name == "send_screenshot":
        svc["wecom"].send(key, "老板，预览截图给您看下，确认后我们继续打包~")
    elif name == "wait_customer_confirm":
        pass  # 无副作用：状态由引擎置 waiting_human，等待 confirm 命令
    elif name == "android_build":
        (_artifacts(base_dir) / "app-release.apk").write_bytes(b"PK-android-apk")
        svc["journal"].append("builder", "android_build", key)
    elif name == "ios_sign":
        svc["signer"].sign(key)
    elif name == "build_submit_materials":
        (_artifacts(base_dir) / "materials.zip").write_bytes(b"PK-materials")
        svc["journal"].append("agent", "materials", key)
    elif name == "upload_store":
        svc["store"].upload(key, "1.0.0")
    else:
        raise ValueError(f"未知步骤 {name}")


def probe_key(task_id: str, name: str) -> str:
    """幂等键：任务 + 步骤唯一确定（源文档 2.3 副作用重放防线）。"""
    return f"{task_id}:{name}"


def journal_anchor(name: str):
    """步骤副作用在 journal 中的 (service, action) 锚点。"""
    return {
        "gen_config":             ("agent", "gen_config"),
        "render_preview":         ("renderer", "render_preview"),
        "send_screenshot":        ("wecom", "send_message"),
        "wait_customer_confirm":  (None, None),   # 无副作用
        "android_build":          ("builder", "android_build"),
        "ios_sign":               ("signer", "sign_ios"),
        "build_submit_materials": ("agent", "materials"),
        "upload_store":           ("store", "upload"),
    }[name]
