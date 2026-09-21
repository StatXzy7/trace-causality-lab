"""服务层测试：对真实监听的标准库 HTTP 服务器发请求。

覆盖导入/拒绝保留、修正/撤销、导出修正副本、样例与空输入、静态页面。
使用 urllib（标准库），端口 0 由系统分配。
"""

from __future__ import annotations

import json
import threading
import unittest
import urllib.error
import urllib.request

from trace_studio.server import create_server


def rec(trace_id, span_id, parent, name, start, end, **extra):
    base = {
        "trace_id": trace_id,
        "span_id": span_id,
        "parent_span_id": parent,
        "name": name,
        "start_ms": start,
        "end_ms": end,
    }
    base.update(extra)
    return base


class ServerHarness:
    def __init__(self):
        self.server = create_server(0)
        self.host, self.port = self.server.server_address[:2]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def base(self):
        return f"http://{self.host}:{self.port}"

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)

    def get(self, path):
        with urllib.request.urlopen(self.base + path, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))

    def post(self, path, body):
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            self.base + path, data=data,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))


class ServerApiTests(unittest.TestCase):
    def setUp(self):
        self.h = ServerHarness()

    def tearDown(self):
        self.h.stop()

    def test_index_page_served(self):
        with urllib.request.urlopen(self.h.base + "/", timeout=5) as resp:
            html = resp.read().decode("utf-8")
        self.assertEqual(resp.status, 200)
        self.assertIn("TRACE", html)
        self.assertIn("应用修正并重新检查", html)

    def test_initial_state_is_empty(self):
        status, state = self.h.get("/api/state")
        self.assertEqual(status, 200)
        self.assertTrue(state["empty"])
        self.assertFalse(state["has_import"])
        self.assertEqual(state["traces"], [])

    def test_load_then_correct_undo_export_flow(self):
        payload = {"records": [
            rec("t", "root", None, "root", 0, 100),
            rec("t", "a", "root", "a", 10, 40),
        ]}
        status, data = self.h.post("/api/load", payload)
        self.assertEqual(status, 200)
        self.assertEqual(data["imported"], 2)
        state = data["state"]
        nodes = {n["span_id"]: n for tr in state["traces"] for n in
                 _flatten(tr)}
        self.assertEqual(nodes["root"]["self_time_ms"], 70.0)
        self.assertEqual(state["total_corrections"], 0)

        # 修正 a 的结束时间 40 -> 90
        status, data = self.h.post("/api/correct", {
            "trace_id": "t", "span_id": "a", "new_end_ms": 90,
        })
        self.assertEqual(status, 200, data)
        nodes = {n["span_id"]: n for tr in data["state"]["traces"] for n in
                 _flatten(tr)}
        self.assertEqual(nodes["a"]["end_ms"], 90)
        self.assertEqual(nodes["root"]["self_time_ms"], 20.0)
        self.assertEqual(data["state"]["total_corrections"], 1)

        # 导出修正副本
        status, exported = self.h.get("/api/export")
        self.assertEqual(status, 200)
        self.assertTrue(exported["corrected"])
        self.assertEqual(len(exported["corrections"]), 1)
        self.assertEqual(
            {r["span_id"]: r["end_ms"] for r in exported["records"]},
            {"root": 100, "a": 90},
        )

        # 撤销后恢复
        status, data = self.h.post("/api/undo", {})
        self.assertEqual(status, 200)
        self.assertIsNotNone(data["undone"])
        nodes = {n["span_id"]: n for tr in data["state"]["traces"] for n in
                 _flatten(tr)}
        self.assertEqual(nodes["a"]["end_ms"], 40)
        self.assertEqual(nodes["root"]["self_time_ms"], 70.0)

        # 原始输入仍在 state 中且未被改动
        originals = data["state"]["original_input"]["records"]
        self.assertEqual(
            {r["span_id"]: r["end_ms"] for r in originals},
            {"root": 100, "a": 40},
        )

    def test_invalid_import_rejected_with_422_and_state_preserved(self):
        self.h.post("/api/load", {"records": [
            rec("t", "root", None, "root", 0, 10),
        ]})
        status, data = self.h.post("/api/load", {"records": [
            rec("t", "x", None, "x", 9, 1),  # end < start
        ]})
        self.assertEqual(status, 422)
        self.assertFalse(data["ok"])
        self.assertIn("早于", data["error"])
        _, state = self.h.get("/api/state")
        self.assertEqual(state["total_spans"], 1)  # 旧数据保留

    def test_cycle_and_conflict_records_rejected(self):
        status, data = self.h.post("/api/load", {"records": [
            rec("t", "a", "b", "a", 0, 1),
            rec("t", "b", "a", "b", 0, 1),
        ]})
        self.assertEqual(status, 422)
        self.assertIn("循环", data["error"])

        status, data = self.h.post("/api/load", {"records": [
            rec("t", "a", None, "x", 0, 1),
            rec("t", "a", None, "x", 0, 2),
        ]})
        self.assertEqual(status, 422)
        self.assertIn("冲突", data["error"])

    def test_parallel_coverage_and_cross_trace_isolation(self):
        status, data = self.h.post("/api/load", {"records": [
            rec("t1", "root", None, "r1", 0, 100),
            rec("t1", "p1", "root", "p1", 30, 60),
            rec("t1", "p2", "root", "p2", 40, 80),
            rec("t2", "root", None, "r2", 0, 10),
            rec("t2", "p1", "root", "p1-other", 2, 8),
        ]})
        self.assertEqual(status, 200)
        state = data["state"]
        by_trace = {tr["trace_id"]: tr for tr in state["traces"]}
        n1 = {n["span_id"]: n for n in _flatten(by_trace["t1"])}
        n2 = {n["span_id"]: n for n in _flatten(by_trace["t2"])}
        # 并行覆盖 [30,60]U[40,80] = 50 => 自身 50
        self.assertEqual(n1["root"]["self_time_ms"], 50.0)
        self.assertEqual(n1["p1"]["self_time_ms"], 30.0)
        self.assertEqual(n1["p2"]["self_time_ms"], 40.0)
        # t2 独立计算
        self.assertEqual(n2["root"]["self_time_ms"], 4.0)
        self.assertEqual(n2["p1"]["self_time_ms"], 6.0)
        # t2 的 p1 父是 t2 的 root，而非 t1 的任何节点
        t2_root = by_trace["t2"]["roots"][0]
        self.assertEqual([c["span_id"] for c in t2_root["children"]], ["p1"])

    def test_identical_duplicates_merged_count(self):
        r = rec("t", "a", None, "a", 0, 1)
        status, data = self.h.post("/api/load", {"records": [r, dict(r), dict(r)]})
        self.assertEqual(status, 200)
        self.assertEqual(data["imported"], 1)

    def test_empty_array_import_shows_empty(self):
        status, data = self.h.post("/api/load", [])
        self.assertEqual(status, 200)
        self.assertTrue(data["state"]["empty"])
        # 空导入也是一次“有效工作”：has_import 为真但 0 条
        self.assertEqual(data["state"]["total_spans"], 0)

    def test_sample_endpoint(self):
        status, data = self.h.post("/api/load-sample", {})
        self.assertEqual(status, 200)
        self.assertGreater(data["imported"], 0)
        traces = {t["trace_id"] for t in data["state"]["traces"]}
        self.assertEqual(len(traces), 2)
        # 样例中应能看到孤立跨度
        orphans = [o for t in data["state"]["traces"] for o in t["orphans"]]
        self.assertTrue(any(o["span_id"] == "bg-retry" for o in orphans))

    def test_bad_json_body_returns_422(self):
        req = urllib.request.Request(
            self.h.base + "/api/load",
            data=b"{not json",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            urllib.request.urlopen(req, timeout=5)
            self.fail("应返回错误状态")
        except urllib.error.HTTPError as exc:
            self.assertEqual(exc.code, 422)
            body = json.loads(exc.read().decode("utf-8"))
            self.assertIn("JSON", body["error"])

    def test_correction_conflict_rejected_state_kept(self):
        self.h.post("/api/load", {"records": [
            rec("t", "root", None, "root", 0, 100),
            rec("t", "a", "root", "a", 0, 10),
            rec("t", "b", "root", "b", 0, 10),
        ]})
        # 把 a 改名为 b => 同 trace 同 id 冲突，应 422 且状态不变
        status, data = self.h.post("/api/correct", {
            "trace_id": "t", "span_id": "a", "new_span_id": "b",
        })
        self.assertEqual(status, 422)
        self.assertIn("冲突", data["error"])
        _, state = self.h.get("/api/state")
        self.assertEqual(state["total_spans"], 3)
        self.assertEqual(state["total_corrections"], 0)


def _flatten(trace):
    out = []

    def walk(n):
        out.append(n)
        for c in n["children"]:
            walk(c)

    for r in trace["roots"]:
        walk(r)
    for o in trace["orphans"]:
        walk(o)
    return out


if __name__ == "__main__":
    unittest.main()
