"""主题雕塑史实审定发布服务入口。

HTTP 接口：
- GET  /health                                  服务身份
- POST /api/commands                            执行一条审定/发布命令（只追加）
- GET  /api/audience?date=&channel=&viewer=     重建某日某渠道观众实际所见
- GET  /api/preview/restricted?date=            受限预展（含争议事实）
- GET  /api/claims/<id>/provenance[?date=]      叙述的批准依据与勘误链
- GET  /api/errata                              公众可见勘误链
- GET  /api/snapshot                            完整状态（运维用）

账本路径由环境变量 SCULPTURE_LEDGER 指定，默认 data/ledger.jsonl。
"""

import argparse
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from review.core import CHANNELS, Ledger, ReviewService, ValidationError, VIEWER_PUBLIC

SERVICE_ID = "sculpture-evidence-review"
SERVICE_NAME = "主题雕塑史实审定"

DEFAULT_LEDGER = os.environ.get("SCULPTURE_LEDGER", "data/ledger.jsonl")


def health_payload():
    """返回稳定的服务身份信息。"""
    return {"status": "ok", "service": SERVICE_ID, "name": SERVICE_NAME}


def build_service(ledger_path: str = DEFAULT_LEDGER) -> ReviewService:
    return ReviewService(Ledger(ledger_path))


class Handler(BaseHTTPRequestHandler):
    """审定发布 HTTP 接口。"""

    service: ReviewService = build_service()

    # -- 工具 -------------------------------------------------------------

    def _send_json(self, code: int, payload: dict):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _query(self) -> dict:
        q = parse_qs(urlparse(self.path).query)
        return {k: v[0] for k, v in q.items()}

    # -- GET --------------------------------------------------------------

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/health":
            self._send_json(200, health_payload())
            return
        try:
            if path == "/api/audience":
                self._handle_audience()
            elif path == "/api/preview/restricted":
                q = self._query()
                self._send_json(200, self.service.restricted_preview(q["date"]))
            elif path == "/api/errata":
                self._send_json(200, self.service.public_errata())
            elif path.startswith("/api/claims/") and path.endswith("/provenance"):
                claim_id = path[len("/api/claims/"):-len("/provenance")]
                self._send_json(200, self.service.claim_provenance(
                    claim_id, self._query().get("date")))
            elif path == "/api/snapshot":
                self._send_json(200, self.service.snapshot())
            else:
                self.send_error(404)
        except ValidationError as exc:
            self._send_json(400, {"error": str(exc)})
        except KeyError as exc:
            self._send_json(400, {"error": f"缺少参数 {exc.args[0]}"})

    def _handle_audience(self):
        q = self._query()
        if "date" not in q:
            raise KeyError("date")
        channel = q.get("channel", "web")
        if channel not in CHANNELS:
            raise ValidationError(f"未知渠道 {channel}")
        viewer = q.get("viewer", VIEWER_PUBLIC)
        self._send_json(200, self.service.audience_view(q["date"], channel, viewer))

    # -- POST -------------------------------------------------------------

    def do_POST(self):
        path = urlparse(self.path).path
        if path != "/api/commands":
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(length) or b"{}")
            name = data.get("command")
            if not name:
                raise ValidationError("缺少 command")
            args = data.get("args", {})
            if data.get("date") and "date" not in args:
                args["date"] = data["date"]
            event = self.service.command(name, args)
            self._send_json(201, {"accepted": True, "event": event})
        except (json.JSONDecodeError, ValueError) as exc:
            self._send_json(400, {"error": f"请求体不是有效 JSON：{exc}"})
        except ValidationError as exc:
            self._send_json(422, {"accepted": False, "error": str(exc)})

    def log_message(self, *_args):
        return


def main():
    parser = argparse.ArgumentParser(description=SERVICE_NAME)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--ledger", default=DEFAULT_LEDGER, help="JSONL 账本路径")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.check:
        assert health_payload()["service"] == SERVICE_ID
        svc = build_service(args.ledger)
        # 账本须可重放：重放结果与事件数一致即视为完好
        svc.snapshot()
        print("基础检查通过")
        return
    Handler.service = build_service(args.ledger)
    ThreadingHTTPServer(("0.0.0.0", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
