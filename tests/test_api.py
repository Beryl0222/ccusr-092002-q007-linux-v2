"""HTTP 接口的冒烟核对。"""

import http.client
import json
import threading
import unittest
from http.server import ThreadingHTTPServer

import service
from domain import ReviewService
from service import Handler


class ApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        service.SERVICE = ReviewService()

    def call(self, method, path, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port)
        payload = json.dumps(body) if body is not None else None
        headers = {"Content-Type": "application/json"} if body is not None else {}
        conn.request(method, path, payload, headers)
        resp = conn.getresponse()
        data = json.loads(resp.read().decode("utf-8"))
        conn.close()
        return resp.status, data

    def test_health(self):
        status, data = self.call("GET", "/health")
        self.assertEqual(status, 200)
        self.assertEqual(data["service"], service.SERVICE_ID)

    def test_review_to_reconstruct_flow(self):
        self.call("POST", "/works", {"id": "SC-1", "title": "湘江记忆"})
        self.call("POST", "/evidence", {
            "id": "EV-1", "work_id": "SC-1", "kind": "archive", "summary": "档案节选",
        })
        self.call("POST", "/claims", {
            "id": "CL-1", "work_id": "SC-1", "text": "人物原型与湘江战役相关",
            "evidence_ids": ["EV-1"], "at": "2026-05-01",
        })
        self.call("POST", "/experts", {"id": "E-1", "name": "专家一"})
        status, rnd = self.call("POST", "/claims/CL-1/signoffs", {"at": "2026-05-02", "quorum": 1})
        self.assertEqual(status, 200)
        self.call("POST", f"/signoffs/{rnd['id']}/votes", {
            "expert_id": "E-1", "decision": "approve", "at": "2026-05-02",
        })
        self.call("POST", f"/signoffs/{rnd['id']}/close", {"at": "2026-05-03"})
        self.call("POST", "/assets", {"id": "IMG-1", "media": "image"})
        self.call("POST", "/licenses", {
            "asset_id": "IMG-1", "media": "image", "channel": "offline",
            "valid_from": "2026-09-01", "valid_to": "2027-08-31",
        })
        status, _ = self.call("POST", "/versions", {
            "items": [{"claim_id": "CL-1", "revision": 1, "asset_ids": ["IMG-1"]}],
            "effective_from": "2026-10-01", "at": "2026-09-20",
        })
        self.assertEqual(status, 200)

        status, view = self.call("GET", "/reconstruct?channel=offline&date=2026-10-10")
        self.assertEqual(status, 200)
        self.assertEqual(view["items"][0]["text"], "人物原型与湘江战役相关")
        self.assertEqual(view["items"][0]["approval"]["round"], rnd["id"])

        status, prov = self.call("GET", "/claims/CL-1/provenance")
        self.assertEqual(status, 200)
        self.assertEqual(prov["rounds"][0]["result"], "approved")

    def test_conflicted_vote_rejected_over_http(self):
        self.call("POST", "/works", {"id": "SC-1", "title": "湘江记忆"})
        self.call("POST", "/claims", {
            "id": "CL-1", "work_id": "SC-1", "text": "待审定叙述",
            "evidence_ids": [], "at": "2026-05-01",
        })
        self.call("POST", "/experts", {"id": "E-9", "name": "专家九", "conflicts": ["SC-1"]})
        _, rnd = self.call("POST", "/claims/CL-1/signoffs", {"at": "2026-05-02", "quorum": 1})
        status, data = self.call("POST", f"/signoffs/{rnd['id']}/votes", {
            "expert_id": "E-9", "decision": "approve", "at": "2026-05-02",
        })
        self.assertEqual(status, 400)
        self.assertEqual(data["type"], "ConflictOfInterest")


if __name__ == "__main__":
    unittest.main()
