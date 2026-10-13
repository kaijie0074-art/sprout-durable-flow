"""测试用本地执行后端（仅 tests/；产品代码 services.py 只含真实 HTTP 适配器）。

经 DURABLE_FLOW_SERVICES_MODULE=local_services 注入：四个副作用以本地磁盘
产物 + journal 落账执行，供崩溃恢复/幂等/不重放回归在无外部服务环境运行。
journal 语义与真实后端一致（append-only + has(key) 幂等探测）。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from durable_flow.services import Journal  # noqa: E402


class LocalWeCom:
    SERVICE, ACTION = "wecom", "send_message"

    def __init__(self, journal):
        self.journal = journal

    def send(self, key, text):
        self.journal.mark_inflight(self.SERVICE, self.ACTION, key)
        self.journal.append(self.SERVICE, self.ACTION, key, {"text": text})

    def sent(self, key):
        return self.journal.has(self.SERVICE, self.ACTION, key)


class LocalRenderer:
    SERVICE, ACTION = "renderer", "render_preview"

    def __init__(self, journal, base_dir):
        self.journal = journal
        self.base = Path(base_dir)

    def render(self, key, html):
        out = self.base / "artifacts" / "preview.html"
        out.parent.mkdir(parents=True, exist_ok=True)
        self.journal.mark_inflight(self.SERVICE, self.ACTION, key)
        out.write_text(html, encoding="utf-8")
        self.journal.append(self.SERVICE, self.ACTION, key, {"file": str(out)})

    def rendered(self, key):
        return self.journal.has(self.SERVICE, self.ACTION, key)


class LocalSigner:
    SERVICE, ACTION = "signer", "sign_ios"

    def __init__(self, journal, base_dir):
        self.journal = journal
        self.base = Path(base_dir)

    def sign(self, key, artifact_path=None):
        out = self.base / "artifacts" / "app.ipa"
        out.parent.mkdir(parents=True, exist_ok=True)
        self.journal.mark_inflight(self.SERVICE, self.ACTION, key)
        out.write_bytes(b"PK\x07\x08signed-ipa")
        self.journal.append(self.SERVICE, self.ACTION, key, {"file": str(out)})

    def signed(self, key):
        return self.journal.has(self.SERVICE, self.ACTION, key)


class LocalStoreUpload:
    SERVICE, ACTION = "store", "upload"

    def __init__(self, journal):
        self.journal = journal

    def upload(self, key, version):
        self.journal.mark_inflight(self.SERVICE, self.ACTION, key)
        self.journal.append(self.SERVICE, self.ACTION, key, {"version": version})

    def uploaded(self, key):
        return self.journal.has(self.SERVICE, self.ACTION, key)


def make_services(base_dir):
    j = Journal(Path(base_dir) / "logs")
    return {
        "journal": j,
        "wecom": LocalWeCom(j),
        "renderer": LocalRenderer(j, base_dir),
        "signer": LocalSigner(j, base_dir),
        "store": LocalStoreUpload(j),
    }
