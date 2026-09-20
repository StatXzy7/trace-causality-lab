"""HTTP API 端到端测试：真实端口上走 urllib，覆盖导入/修正/撤销/导出/样例。

使用端口 0 绑定，测试结束自动关闭，不依赖任何第三方包。
"""

from __future__ import annotations

import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from trace_studio.server import TraceRequestHandler
from trace_studio.store import TraceStore


def span(trace, sid, parent, name, start, end, **extra):
    record = {
        "traceId": trace,
        "spanId": sid,
        "parentSpanId": parent,
        "name": name,
        "startMs": start,
        "endMs": end,
    }
    record.update(extra)
    return record


class HttpApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.store = TraceStore()

        class Handler(TraceRequestHandler):
            pass

        Handler.store = cls.store
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.port = cls.server.server_address[1]
        cls.base = f"http://127.0.0.1:{cls.port}"
        cls.thread = threading.Thread(
            target=cls.server.serve_forever, name="test-http", daemon=True
        )
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=3)

    def setUp(self):
        self.store.clear()

    def _request(self, method, path, payload=None):
        data = None
        headers = {}
        if payload is not None:
            data = json.dumps(payload, allow_nan=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(
            f"{self.base}{path}", data=data, headers=headers, method=method
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    # ------------------------------------------------------------------
    def test_initial_state_is_empty(self):
        status, body = self._request("GET", "/api/state")
        self.assertEqual(200, status)
        self.assertTrue(body["empty"])
        self.assertEqual([], body["traces"])

    def test_index_page_served(self):
        status, body = self._request_raw("/")
        self.assertEqual(200, status)
        self.assertIn("trace_studio", body)

    def test_static_assets_served(self):
        for path in ("/web/app.js", "/web/style.css"):
            status, body = self._request_raw(path)
            self.assertEqual(200, status, path)
            self.assertGreater(len(body), 100)

    def _request_raw(self, path):
        with urllib.request.urlopen(f"{self.base}{path}", timeout=5) as resp:
            return resp.status, resp.read().decode("utf-8")

    def test_import_valid_then_state_has_tree(self):
        records = [
            span("t", "r", None, "root", 0, 100),
            span("t", "a", "r", "a", 10, 60),
            span("t", "b", "r", "b", 40, 90),
        ]
        status, body = self._request("POST", "/api/import", {"records": records})
        self.assertEqual(200, status)
        self.assertTrue(body["ok"])
        state = body["state"]
        self.assertEqual(1, state["traceCount"])
        root = state["traces"][0]["roots"][0]
        # 并行 [10,60)+[40,90) 并集 80 → 自身 20
        self.assertEqual(80.0, root["childCoverageMs"])
        self.assertEqual(20.0, root["selfMs"])
        self.assertEqual(["a", "b"], [c["spanId"] for c in root["children"]])

    def test_import_rejected_keeps_previous_state(self):
        self._request("POST", "/api/import",
                      {"records": [span("t", "r", None, "good", 0, 10)]})
        status, body = self._request("POST", "/api/import", {
            "records": [span("t", "x", None, "bad", 9, 1)]
        })
        self.assertEqual(400, status)
        self.assertFalse(body["ok"])
        self.assertTrue(any(i["code"] == "end_before_start" for i in body["issues"]))
        # 此前数据保留
        _, state_body = self._request("GET", "/api/state")
        self.assertEqual(1, state_body["spanCount"])
        self.assertEqual("good",
                         state_body["traces"][0]["roots"][0]["name"])

    def test_conflict_import_returns_400(self):
        status, body = self._request("POST", "/api/import", {
            "records": [span("t", "r", None, "r", 0, 10),
                        span("t", "r", None, "r", 0, 11)]
        })
        self.assertEqual(400, status)
        self.assertEqual("conflict", body["issues"][0]["code"])

    def test_correction_undo_export_roundtrip(self):
        records = [
            span("t", "r", None, "root", 0, 100),
            span("t", "a", "r", "a", 10, 40),
        ]
        self._request("POST", "/api/import", {"records": records})

        status, body = self._request("POST", "/api/correct", {
            "traceId": "t", "spanId": "a",
            "startMs": 10, "endMs": 60, "parentSpanId": "r",
            "reason": "clock skew",
        })
        self.assertEqual(200, status, body)
        self.assertTrue(body["state"]["canUndo"])
        self.assertEqual(1, body["state"]["correctionCount"])

        # 导出修正副本
        status, bundle = self._request("GET", "/api/export")
        self.assertEqual(200, status)
        a_record = next(r for r in bundle["records"] if r["spanId"] == "a")
        self.assertEqual(60.0, a_record["endMs"])
        self.assertEqual("clock skew", bundle["corrections"][0]["reason"])

        # 撤销后恢复
        status, body = self._request("POST", "/api/undo", {})
        self.assertEqual(200, status)
        self.assertEqual("a", body["undone"]["spanId"])
        self.assertFalse(body["state"]["canUndo"])
        _, bundle2 = self._request("GET", "/api/export")
        self.assertEqual(40.0, next(
            r for r in bundle2["records"] if r["spanId"] == "a")["endMs"])

    def test_correction_cycle_rejected_over_http(self):
        records = [
            span("t", "r", None, "root", 0, 100),
            span("t", "a", "r", "a", 10, 40),
        ]
        self._request("POST", "/api/import", {"records": records})
        status, body = self._request("POST", "/api/correct", {
            "traceId": "t", "spanId": "r",
            "startMs": 0, "endMs": 100, "parentSpanId": "a",
        })
        self.assertEqual(400, status)
        self.assertEqual("cycle", body["issues"][0]["code"])

    def test_sample_endpoint_contains_orphan_and_zero_span(self):
        status, body = self._request("GET", "/api/sample")
        self.assertEqual(200, status)
        self.assertGreaterEqual(body["traceCount"], 2)
        self.assertGreaterEqual(body["orphanCount"], 1)
        # T-1000 存在零时长跨度 w3 与错误 e1
        t1000 = next(t for t in body["traces"] if t["traceId"] == "T-1000")
        self.assertEqual(1, t1000["errorCount"])
        all_nodes = []

        def walk(nodes):
            for node in nodes:
                all_nodes.append(node)
                walk(node["children"])

        walk(t1000["roots"])
        walk(t1000["orphans"])
        w3 = next(n for n in all_nodes if n["spanId"] == "w3")
        self.assertEqual(0.0, w3["durationMs"])

    def test_clear_endpoint(self):
        self._request("POST", "/api/import",
                      {"records": [span("t", "r", None, "r", 0, 1)]})
        status, body = self._request("POST", "/api/clear", {})
        self.assertEqual(200, status)
        self.assertTrue(body["state"]["empty"])

    def test_bad_json_body_returns_400(self):
        req = urllib.request.Request(
            f"{self.base}/api/import",
            data=b"not-json{",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            urllib.request.urlopen(req, timeout=5)
            self.fail("应当返回 400")
        except urllib.error.HTTPError as exc:
            self.assertEqual(400, exc.code)

    def test_unknown_route_404(self):
        try:
            urllib.request.urlopen(f"{self.base}/nope", timeout=5)
            self.fail("应当 404")
        except urllib.error.HTTPError as exc:
            self.assertEqual(404, exc.code)

    def test_path_traversal_blocked(self):
        try:
            urllib.request.urlopen(
                f"{self.base}/web/../server.py", timeout=5)
            self.fail("路径穿越应被阻止")
        except urllib.error.HTTPError as exc:
            self.assertIn(exc.code, (403, 404))


if __name__ == "__main__":
    unittest.main()
