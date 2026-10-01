"""主题雕塑史实审定的服务入口与 HTTP 接口。"""

import argparse
import json
import re
from dataclasses import asdict, is_dataclass
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from domain import DomainError, ReviewService

SERVICE_ID = "sculpture-evidence-review"
SERVICE_NAME = "主题雕塑史实审定"

SERVICE = ReviewService()


def health_payload():
    """返回稳定的服务身份信息。"""
    return {"status": "ok", "service": SERVICE_ID, "name": SERVICE_NAME}


def _json_default(value):
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (set, frozenset)):
        return sorted(value)
    if is_dataclass(value):
        return asdict(value)
    raise TypeError(f"不可序列化: {type(value)!r}")


ROUTES = [
    ("POST", r"^/works$", lambda b, q: SERVICE.register_work(**b)),
    ("POST", r"^/assets$", lambda b, q: SERVICE.register_asset(**b)),
    ("POST", r"^/evidence$", lambda b, q: SERVICE.register_evidence(**b)),
    ("POST", r"^/experts$", lambda b, q: SERVICE.register_expert(**b)),
    ("POST", r"^/claims$", lambda b, q: SERVICE.register_claim(**b)),
    (
        "POST",
        r"^/claims/(?P<claim_id>[^/]+)/revisions$",
        lambda b, q, claim_id: SERVICE.revise_claim(claim_id, **b),
    ),
    (
        "POST",
        r"^/claims/(?P<claim_id>[^/]+)/signoffs$",
        lambda b, q, claim_id: SERVICE.open_signoff(claim_id, **b),
    ),
    (
        "POST",
        r"^/signoffs/(?P<round_id>[^/]+)/votes$",
        lambda b, q, round_id: SERVICE.cast_vote(round_id, **b),
    ),
    (
        "POST",
        r"^/signoffs/(?P<round_id>[^/]+)/close$",
        lambda b, q, round_id: SERVICE.close_signoff(round_id, **b),
    ),
    ("POST", r"^/licenses$", lambda b, q: SERVICE.grant_license(**b)),
    (
        "POST",
        r"^/licenses/(?P<license_id>[^/]+)/revoke$",
        lambda b, q, license_id: SERVICE.revoke_license(license_id, **b),
    ),
    ("POST", r"^/versions$", lambda b, q: SERVICE.create_version(**b)),
    ("POST", r"^/previews$", lambda b, q: SERVICE.open_preview(**b)),
    ("POST", r"^/releases$", lambda b, q: SERVICE.publish_release(**b)),
    (
        "GET",
        r"^/claims/(?P<claim_id>[^/]+)/errata$",
        lambda b, q, claim_id: SERVICE.errata_chain(claim_id),
    ),
    (
        "GET",
        r"^/claims/(?P<claim_id>[^/]+)/provenance$",
        lambda b, q, claim_id: SERVICE.provenance(claim_id),
    ),
    (
        "GET",
        r"^/reconstruct$",
        lambda b, q: SERVICE.reconstruct(q["channel"][0], q["date"][0]),
    ),
]


class Handler(BaseHTTPRequestHandler):
    """JSON 接口：审定流程、权利管理与按日期重建。"""

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def _dispatch(self, method):
        parsed = urlparse(self.path)
        try:
            if method == "GET" and parsed.path == "/health":
                return self._send(200, health_payload())
            body = {}
            if method == "POST":
                length = int(self.headers.get("Content-Length") or 0)
                if length:
                    body = json.loads(self.rfile.read(length).decode("utf-8"))
            query = parse_qs(parsed.query)
            for route_method, pattern, handler in ROUTES:
                if route_method != method:
                    continue
                match = re.match(pattern, parsed.path)
                if match:
                    result = handler(body, query, **match.groupdict())
                    return self._send(200, result if result is not None else {"ok": True})
            self._send(404, {"error": "not found"})
        except DomainError as exc:
            self._send(400, {"error": str(exc), "type": type(exc).__name__})
        except (KeyError, TypeError, ValueError) as exc:
            self._send(400, {"error": f"请求参数无效: {exc}"})

    def _send(self, code, payload):
        body = json.dumps(payload, ensure_ascii=False, default=_json_default).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        return


def main():
    parser = argparse.ArgumentParser(description=SERVICE_NAME)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.check:
        assert health_payload()["service"] == SERVICE_ID
        assert ReviewService() is not None
        print("基础检查通过")
        return
    ThreadingHTTPServer(("0.0.0.0", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
