"""Mock 外部服务：以追加日志（journal.jsonl）模拟副作用。

副作用 = 发消息 / 渲染 / 签名 / 上传 —— 一旦发生不可收回，因此全部
落日志；journal 同时承担"幂等探测"的查询语义：执行前先查 has(key)，
已存在即视为已发生（源文档 2.3 前置探测）。
"""
import json
import time
from pathlib import Path


class Journal:
    """副作用日志：append-only + 按唯一键查询。"""

    def __init__(self, base_dir):
        self.dir = Path(base_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / "journal.jsonl"

    def append(self, service: str, action: str, key: str, payload: dict | None = None):
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


class WeCom:
    """客户消息通道（外发类副作用：重放 = 客户收到两条一样的消息）。"""
    SERVICE, ACTION = "wecom", "send_message"

    def __init__(self, journal: Journal):
        self.journal = journal

    def send(self, key: str, text: str):
        self.journal.append(self.SERVICE, self.ACTION, key, {"text": text})

    def sent(self, key: str) -> bool:
        return self.journal.has(self.SERVICE, self.ACTION, key)


class Renderer:
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
    """iOS 签名机（最高危：证书在稀缺签名环境上使用）。"""
    SERVICE, ACTION = "signer", "sign_ios"

    def __init__(self, journal: Journal, base_dir):
        self.journal = journal
        self.base = Path(base_dir)

    def sign(self, key: str):
        out = self.base / "artifacts" / "app.ipa"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"PK\x07\x08signed-ipa")
        self.journal.append(self.SERVICE, self.ACTION, key, {"file": str(out)})

    def signed(self, key: str) -> bool:
        return self.journal.has(self.SERVICE, self.ACTION, key)


class Store:
    """应用商店后台上传（高危：重复上传 = 后台出现重复版本）。"""
    SERVICE, ACTION = "store", "upload"

    def __init__(self, journal: Journal):
        self.journal = journal

    def upload(self, key: str, version: str):
        self.journal.append(self.SERVICE, self.ACTION, key, {"version": version})

    def uploaded(self, key: str) -> bool:
        return self.journal.has(self.SERVICE, self.ACTION, key)


def make_services(base_dir):
    j = Journal(Path(base_dir) / "logs")
    return {
        "journal": j,
        "wecom": WeCom(j),
        "renderer": Renderer(j, base_dir),
        "signer": Signer(j, base_dir),
        "store": Store(j),
    }
