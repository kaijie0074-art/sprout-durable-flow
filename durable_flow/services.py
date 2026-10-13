"""外部副作用服务：真实 HTTP 适配器 + journal 审计/幂等探测。

设计（源文档 2.3 前置探测）：
  - 副作用 = 发消息 / 渲染 / 签名 / 上传——一旦发生不可收回，因此全部落
    journal；journal 同时承担"幂等探测"的查询语义：执行前先查 has(key)，
    已存在即视为已发生。
  - 外发服务走真实 HTTP（企业微信 webhook / 签名机 / 商店后台），凭证与
    地址走环境变量，未配置时执行到该步显式报错并给配置指引——绝不静默
    跳过副作用。
  - Renderer 为本地渲染（HTML 预览写盘），是真实产物，非外发。

环境变量：
  WECOM_WEBHOOK_URL    企业微信群机器人 webhook（客户消息通道）
  SIGNER_URL           iOS 签名机服务（POST 构建→返回签名 ipa 字节）
  STORE_UPLOAD_URL     应用商店后台上传接口
  DURABLE_FLOW_SERVICES_MODULE  可选：动态加载替用 services 工厂
                                （测试注入本地执行后端用；见 tests/local_services.py）
"""
import importlib
import json
import os
import urllib.request
from pathlib import Path


class ServiceNotConfiguredError(RuntimeError):
    """外发服务未配置。信息包含配置指引。"""


class Journal:
    """副作用日志：append-only + 按唯一键查询。"""

    def __init__(self, base_dir):
        self.dir = Path(base_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / "journal.jsonl"

    def append(self, service: str, action: str, key: str, payload: dict | None = None):
        import time

        entry = {"ts": round(time.time(), 3), "service": service, "action": action,
                 "key": key, "payload": payload or {}}
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def entries(self, service: str = None, action: str = None, key: str = None) -> list:
        if not self.path.exists():
            return []
        out = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            e = json.loads(line)
            if service and e["service"] != service:
                continue
            if action and e["action"] != action:
                continue
            if key and e["key"] != key:
                continue
            out.append(e)
        return out

    def has(self, service: str, action: str, key: str) -> bool:
        return bool(self.entries(service=service, action=action, key=key))


def _post_json(url: str, payload: dict, timeout: int = 60) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


class WeCom:
    """客户消息通道：企业微信群机器人 webhook（重放 = 客户收到两条一样的消息）。"""
    SERVICE, ACTION = "wecom", "send_message"

    def __init__(self, journal: Journal):
        self.journal = journal
        self.webhook = os.environ.get("WECOM_WEBHOOK_URL", "")

    def send(self, key: str, text: str):
        if not self.webhook:
            raise ServiceNotConfiguredError(
                "客户消息通道未配置：请设置 WECOM_WEBHOOK_URL（企业微信群机器人 webhook）。"
                "未配置时拒绝静默跳过外发副作用。"
            )
        _post_json(self.webhook, {"msgtype": "text", "text": {"content": text}})
        self.journal.append(self.SERVICE, self.ACTION, key, {"text": text})

    def sent(self, key: str) -> bool:
        return self.journal.has(self.SERVICE, self.ACTION, key)


class Renderer:
    """本地渲染：HTML 预览写盘（真实产物，非外发）。"""
    SERVICE, ACTION = "renderer", "render_preview"

    def __init__(self, journal: Journal, base_dir):
        self.journal = journal
        self.base = Path(base_dir)

    def render(self, key: str, html: str):
        out = self.base / "artifacts" / "preview.html"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(html, encoding="utf-8")
        self.journal.append(self.SERVICE, self.ACTION, key, {"file": str(out)})

    def rendered(self, key: str) -> bool:
        return self.journal.has(self.SERVICE, self.ACTION, key)


class Signer:
    """iOS 签名机（最高危：证书在稀缺签名环境上使用）。POST 构建 → 签名 ipa 落盘。"""
    SERVICE, ACTION = "signer", "sign_ios"

    def __init__(self, journal: Journal, base_dir):
        self.journal = journal
        self.base = Path(base_dir)
        self.url = os.environ.get("SIGNER_URL", "")

    def sign(self, key: str, artifact_path: str | None = None):
        if not self.url:
            raise ServiceNotConfiguredError(
                "签名机服务未配置：请设置 SIGNER_URL（POST 构建→返回签名 ipa）。"
                "未配置时拒绝静默跳过高危副作用。"
            )
        req = urllib.request.Request(
            self.url, data=json.dumps({"key": key, "artifact": artifact_path or ""}).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=300) as resp:
            ipa = resp.read()
        out = self.base / "artifacts" / "app.ipa"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(ipa)
        self.journal.append(self.SERVICE, self.ACTION, key, {"file": str(out)})

    def signed(self, key: str) -> bool:
        return self.journal.has(self.SERVICE, self.ACTION, key)


class StoreUpload:
    """应用商店后台上传（高危：重复上传 = 后台出现重复版本）。"""
    SERVICE, ACTION = "store", "upload"

    def __init__(self, journal: Journal):
        self.journal = journal
        self.url = os.environ.get("STORE_UPLOAD_URL", "")

    def upload(self, key: str, version: str):
        if not self.url:
            raise ServiceNotConfiguredError(
                "商店上传接口未配置：请设置 STORE_UPLOAD_URL。"
                "未配置时拒绝静默跳过高危副作用。"
            )
        _post_json(self.url, {"key": key, "version": version})
        self.journal.append(self.SERVICE, self.ACTION, key, {"version": version})

    def uploaded(self, key: str) -> bool:
        return self.journal.has(self.SERVICE, self.ACTION, key)


def make_services(base_dir):
    """组装副作用服务。

    默认真实 HTTP 适配器（env-gated）；设置 DURABLE_FLOW_SERVICES_MODULE 时
    动态加载替用工厂（测试注入本地执行后端）。
    """
    mod_name = os.environ.get("DURABLE_FLOW_SERVICES_MODULE", "")
    if mod_name:
        mod = importlib.import_module(mod_name)
        return mod.make_services(base_dir)
    j = Journal(Path(base_dir) / "logs")
    return {
        "journal": j,
        "wecom": WeCom(j),
        "renderer": Renderer(j, base_dir),
        "signer": Signer(j, base_dir),
        "store": StoreUpload(j),
    }
