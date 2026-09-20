"""修正、撤销、导出与“拒绝时保留此前有效工作”测试。"""

from __future__ import annotations

import json
import unittest

from trace_studio.corrections import Correction
from trace_studio.model import ERR_UNKNOWN_SPAN
from trace_studio.parser import (
    ERR_CYCLE,
    ERR_END_BEFORE_START,
    ERR_NON_FINITE_TIME,
    parse_records,
)
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


def load(store, records):
    result = parse_records(records)
    assert result.ok, result.issues
    return store.load_parse_result(result)


def index(store):
    out = {}
    for s in store.effective_spans():
        out[(s.trace_id, s.span_id)] = s
    return out


class CorrectionTests(unittest.TestCase):
    def setUp(self):
        self.store = TraceStore()
        load(self.store, [
            span("t", "r", None, "root", 0, 100),
            span("t", "a", "r", "a", 10, 40),
            span("t", "b", "r", "b", 50, 90),
        ])

    def test_correct_times_updates_tree_and_self_time(self):
        # 把 a 从 [10,40) 改为 [10,60)，与 b [50,90) 重叠 10ms
        action = self.store.apply_correction(
            Correction("t", "a", 10, 60, "r", "对齐时钟")
        )
        self.assertTrue(action.ok, action.issues)
        forest = self.store.forest()
        root = forest[0].roots[0]
        child_ids = {c.span.span_id for c in root.children}
        self.assertEqual({"a", "b"}, child_ids)
        # 并集 [10,90)=80（不是 50+40=90），自身 20
        self.assertEqual(80.0, root.child_coverage_ms)
        self.assertEqual(20.0, root.self_ms)

    def test_correct_parent_relinks_node(self):
        # b 原是 r 的孩子，改挂到 a 下
        action = self.store.apply_correction(
            Correction("t", "b", 50, 90, "a", "错误的上报父级")
        )
        self.assertTrue(action.ok, action.issues)
        root = self.store.forest()[0].roots[0]
        self.assertEqual(["a"], [c.span.span_id for c in root.children])
        a_node = root.children[0]
        self.assertEqual(["b"], [c.span.span_id for c in a_node.children])
        # a 的自身耗时：30 - b 裁剪到 [10,40) 的覆盖（交集空）= 30
        self.assertEqual(30.0, a_node.self_ms)

    def test_correct_parent_to_null_moves_to_root(self):
        action = self.store.apply_correction(
            Correction("t", "a", 10, 40, None)
        )
        self.assertTrue(action.ok, action.issues)
        tree = self.store.forest()[0]
        root_ids = {n.span.span_id for n in tree.roots}
        self.assertEqual({"r", "a"}, root_ids)

    def test_end_before_start_correction_rejected(self):
        before = list(self.store.effective_spans())
        action = self.store.apply_correction(
            Correction("t", "a", 60, 10, "r")
        )
        self.assertFalse(action.ok)
        self.assertEqual(ERR_END_BEFORE_START, action.issues[0].code)
        # 有效数据保持不变
        self.assertEqual(
            [(s.trace_id, s.span_id, s.start_ms, s.end_ms) for s in before],
            [(s.trace_id, s.span_id, s.start_ms, s.end_ms)
             for s in self.store.effective_spans()],
        )

    def test_non_finite_correction_rejected(self):
        action = self.store.apply_correction(
            Correction("t", "a", float("inf"), 40, "r")
        )
        self.assertFalse(action.ok)
        self.assertEqual(ERR_NON_FINITE_TIME, action.issues[0].code)

    def test_unknown_span_correction_rejected(self):
        action = self.store.apply_correction(
            Correction("t", "nope", 0, 1, None)
        )
        self.assertFalse(action.ok)
        self.assertEqual(ERR_UNKNOWN_SPAN, action.issues[0].code)

    def test_cycle_inducing_correction_rejected(self):
        # 让 r 的父变成 a，而 a 的父是 r → 环
        action = self.store.apply_correction(
            Correction("t", "r", 0, 100, "a")
        )
        self.assertFalse(action.ok)
        self.assertEqual(ERR_CYCLE, action.issues[0].code)
        # r 仍是根
        self.assertIsNone(index(self.store)[("t", "r")].parent_id)

    def test_correct_to_missing_parent_in_same_trace_makes_orphan(self):
        action = self.store.apply_correction(
            Correction("t", "a", 10, 40, "vanished")
        )
        self.assertTrue(action.ok, action.issues)
        tree = self.store.forest()[0]
        self.assertEqual(1, len(tree.orphans))
        self.assertEqual("a", tree.orphans[0].span.span_id)
        # r 失去子节点 a
        root = tree.roots[0]
        self.assertEqual(["b"], [c.span.span_id for c in root.children])

    def test_self_parent_correction_rejected(self):
        action = self.store.apply_correction(
            Correction("t", "a", 10, 40, "a")
        )
        self.assertFalse(action.ok)
        self.assertEqual(ERR_CYCLE, action.issues[0].code)

    def test_cross_trace_parent_reference_creates_orphan_not_link(self):
        # 另一追踪 t2 有 z；a 在 t1，试图指向 t2 的 z
        load(self.store, [
            span("t", "r", None, "root", 0, 100),
            span("t", "a", "r", "a", 10, 40),
            span("t", "b", "r", "b", 50, 90),
            span("t2", "z", None, "other", 0, 5),
        ])
        action = self.store.apply_correction(
            Correction("t", "a", 10, 40, "z")
        )
        self.assertTrue(action.ok, action.issues)
        by_trace = {tree.trace_id: tree for tree in self.store.forest()}
        self.assertEqual(1, len(by_trace["t"].orphans))
        self.assertEqual("a", by_trace["t"].orphans[0].span.span_id)
        # t2 的 z 没有因此多出孩子
        self.assertEqual([], by_trace["t2"].roots[0].children)


class UndoTests(unittest.TestCase):
    def setUp(self):
        self.store = TraceStore()
        load(self.store, [
            span("t", "r", None, "root", 0, 100),
            span("t", "a", "r", "a", 10, 40),
        ])

    def test_undo_restores_previous_times(self):
        self.store.apply_correction(Correction("t", "a", 10, 50, "r"))
        self.assertEqual(50.0, index(self.store)[("t", "a")].end_ms)
        popped = self.store.undo()
        self.assertIsNotNone(popped)
        self.assertEqual(40.0, index(self.store)[("t", "a")].end_ms)
        self.assertFalse(self.store.state_dict()["canUndo"])

    def test_undo_lifo_multiple_corrections(self):
        self.store.apply_correction(Correction("t", "a", 10, 41, "r"))
        self.store.apply_correction(Correction("t", "a", 10, 42, "r"))
        self.store.apply_correction(Correction("t", "a", 10, 43, "r"))
        self.assertEqual(43.0, index(self.store)[("t", "a")].end_ms)
        self.store.undo()
        self.assertEqual(42.0, index(self.store)[("t", "a")].end_ms)
        self.store.undo()
        self.assertEqual(41.0, index(self.store)[("t", "a")].end_ms)
        self.store.undo()
        self.assertEqual(40.0, index(self.store)[("t", "a")].end_ms)
        self.assertIsNone(self.store.undo())

    def test_rejected_correction_not_pushed_to_history(self):
        self.store.apply_correction(Correction("t", "a", 99, 1, "r"))
        self.assertFalse(self.store.state_dict()["canUndo"])
        self.assertEqual(0, self.store.state_dict()["correctionCount"])


class OriginalImmutabilityTests(unittest.TestCase):
    def test_original_record_unchanged_after_correction(self):
        store = TraceStore()
        records = [
            span("t", "r", None, "root", 0, 100, service="gw"),
            span("t", "a", "r", "a", 10, 40, service="worker"),
        ]
        load(store, records)
        store.apply_correction(Correction("t", "a", 10, 80, "r"))

        state = store.state_dict()
        original = next(r for r in state["originals"] if r["spanId"] == "a")
        # 原始记录保持 40
        self.assertEqual(40, original["endMs"])
        self.assertEqual("r", original["parentSpanId"])
        # 原始输入列表本身也没被改动
        self.assertEqual(40, records[1]["endMs"])

        effective = index(store)[("t", "a")]
        self.assertEqual(80.0, effective.end_ms)
        # 额外字段在修正副本中保留
        self.assertEqual("worker", effective.raw["service"])


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.store = TraceStore()
        load(self.store, [
            span("t", "r", None, "root", 0, 100),
            span("t", "a", "r", "a", 10, 40, service="w"),
        ])

    def test_export_records_reflects_corrections(self):
        self.store.apply_correction(Correction("t", "a", 10, 55, "r", "fix"))
        exported = self.store.export_records()
        a = next(r for r in exported if r["spanId"] == "a")
        self.assertEqual(55.0, a["endMs"])
        self.assertEqual("w", a["service"])  # 额外字段保留
        # 导出内容可被再次导入（往返一致）
        again = parse_records(exported)
        self.assertTrue(again.ok, again.issues)
        self.assertEqual(2, len(again.spans))

    def test_export_bundle_includes_correction_metadata(self):
        self.store.apply_correction(Correction("t", "a", 10, 55, "r", "fix clock"))
        bundle = self.store.export_bundle()
        self.assertEqual(2, len(bundle["records"]))
        self.assertEqual(1, len(bundle["corrections"]))
        self.assertEqual("fix clock", bundle["corrections"][0]["reason"])
        flagged = [r for r in bundle["records"] if r.get("_correctionApplied")]
        self.assertEqual(["a"], [r["spanId"] for r in flagged])
        # 导出是副本：修改导出对象不影响存储
        bundle["records"][0]["endMs"] = 9999
        self.assertNotEqual(
            9999, index(self.store)[(bundle["records"][0]["traceId"],
                                     bundle["records"][0]["spanId"])].end_ms
        )

    def test_export_json_serializable(self):
        self.store.apply_correction(Correction("t", "a", 10, 55, None))
        # 不允许 NaN/Infinity 泄漏到导出
        payload = json.dumps(self.store.export_bundle(), allow_nan=False)
        self.assertIn("records", payload)


class PreservePreviousWorkTests(unittest.TestCase):
    def test_rejected_import_preserves_previous_valid_data(self):
        store = TraceStore()
        load(store, [span("t", "r", None, "good", 0, 10)])

        bad = parse_records([
            span("t", "x", None, "bad", 10, 5),  # end < start
        ])
        action = store.load_parse_result(bad)
        self.assertFalse(action.ok)
        state = store.state_dict()
        self.assertEqual(1, state["spanCount"])
        self.assertEqual("good", store.forest()[0].roots[0].span.name)

    def test_conflict_import_preserves_previous_data_and_corrections(self):
        store = TraceStore()
        load(store, [span("t", "r", None, "root", 0, 10)])
        store.apply_correction(Correction("t", "r", 0, 20, None))

        bad = parse_records([
            span("t", "r", None, "root", 0, 11),  # 同编号冲突
            span("t", "r", None, "root", 0, 12),
        ])
        action = store.load_parse_result(bad)
        self.assertFalse(action.ok)
        # 此前的记录与修正历史都保留
        self.assertEqual(20.0, index(store)[("t", "r")].end_ms)
        self.assertTrue(store.state_dict()["canUndo"])

    def test_clear_resets_to_empty(self):
        store = TraceStore()
        load(store, [span("t", "r", None, "root", 0, 10)])
        store.clear()
        state = store.state_dict()
        self.assertTrue(state["empty"])
        self.assertEqual([], state["traces"])
        self.assertEqual([], store.export_records())


class StateDictTests(unittest.TestCase):
    def test_empty_state_shape(self):
        store = TraceStore()
        state = store.state_dict()
        self.assertTrue(state["empty"])
        self.assertEqual(0, state["spanCount"])
        self.assertEqual([], state["traces"])
        self.assertFalse(state["canUndo"])

    def test_state_counts(self):
        store = TraceStore()
        load(store, [
            span("t", "r", None, "root", 0, 100),
            span("t", "e", "r", "err", 10, 20, error=True),
            span("t", "x", "ghost", "orphan", 30, 40),
        ])
        state = store.state_dict()
        self.assertFalse(state["empty"])
        self.assertEqual(1, state["traceCount"])
        self.assertEqual(3, state["spanCount"])
        self.assertEqual(1, state["errorCount"])
        self.assertEqual(1, state["orphanCount"])


if __name__ == "__main__":
    unittest.main()
