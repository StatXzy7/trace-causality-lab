"""领域模型测试：导入校验、建树、并行覆盖耗时、孤立/异常、修正与导出。

断言具体调用关系与耗时数值，而非仅检查“页面有响应”。
"""

from __future__ import annotations

import copy
import unittest

from trace_studio.model import (
    SpanKey,
    Studio,
    TraceValidationError,
    build_view,
    merged_coverage,
    parse_payload,
    validate_records,
)
from trace_studio import samples


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


def node_map(view, trace_id):
    trace = next(t for t in view["traces"] if t["trace_id"] == trace_id)
    out = {}

    def walk(n):
        out[n["span_id"]] = n
        for c in n["children"]:
            walk(c)

    for r in trace["roots"]:
        walk(r)
    for o in trace["orphans"]:
        walk(o)
    return out, trace


class TestMergedCoverage(unittest.TestCase):
    def test_disjoint_intervals_sum(self):
        self.assertEqual(merged_coverage([(0, 10), (20, 30)]), 20.0)

    def test_overlapping_parallel_children_counted_once(self):
        # 两个并行子调用 0-60 与 40-80 => 并集 0-80 = 80，而不是 60+40=100
        self.assertEqual(merged_coverage([(0, 60), (40, 80)]), 80.0)

    def test_fully_nested_interval(self):
        self.assertEqual(merged_coverage([(0, 100), (10, 20), (30, 40)]), 100.0)

    def test_touching_intervals_merge(self):
        # 端点相接 [0,10)+[10,20) 不重叠也不留缝 => 20
        self.assertEqual(merged_coverage([(0, 10), (10, 20)]), 20.0)

    def test_zero_duration_intervals_contribute_nothing(self):
        self.assertEqual(merged_coverage([(5, 5), (0, 0)]), 0.0)

    def test_unsorted_input(self):
        self.assertEqual(merged_coverage([(50, 60), (0, 30), (20, 55)]), 60.0)


class TestTreeAndSelfTime(unittest.TestCase):
    def test_parallel_children_self_time(self):
        records = [
            rec("t", "root", None, "root", 0, 100),
            rec("t", "a", "root", "seq-a", 10, 20),       # 10
            rec("t", "p1", "root", "par-1", 30, 60),      # 并行 30
            rec("t", "p2", "root", "par-2", 40, 80),      # 与 p1 重叠 20
            rec("t", "tail", "root", "tail", 90, 95),     # 5
        ]
        view = build_view(records)
        nodes, trace = node_map(view, "t")
        # 直接子覆盖并集：[10,20) U [30,80) U [90,95) = 10+50+5 = 65
        self.assertEqual(nodes["root"]["child_coverage_ms"], 65.0)
        self.assertEqual(nodes["root"]["self_time_ms"], 35.0)
        # 叶子自身耗时 = 自身时长
        self.assertEqual(nodes["p1"]["self_time_ms"], 30.0)
        self.assertEqual(nodes["p2"]["self_time_ms"], 40.0)
        # 调用关系：root 有 4 个直接子调用
        root = trace["roots"][0]
        self.assertEqual([c["span_id"] for c in root["children"]],
                         ["a", "p1", "p2", "tail"])

    def test_nested_descendants_not_double_counted(self):
        # 孙跨度完全落在子跨度内：root 的自身耗时只扣直接子 a（30），不再扣孙
        records = [
            rec("t", "root", None, "root", 0, 100),
            rec("t", "a", "root", "a", 10, 40),
            rec("t", "a1", "a", "a1", 20, 30),
        ]
        view = build_view(records)
        nodes, _ = node_map(view, "t")
        self.assertEqual(nodes["root"]["self_time_ms"], 70.0)
        self.assertEqual(nodes["a"]["self_time_ms"], 20.0)  # 30 - 10
        self.assertEqual(nodes["a1"]["self_time_ms"], 10.0)

    def test_child_partially_outside_parent_clipped(self):
        # 子跨度超出父范围：保留原始时间，覆盖只按父范围 [10,20] 裁剪
        records = [
            rec("t", "p", None, "p", 10, 20),
            rec("t", "c", "p", "c", 15, 40),
        ]
        view = build_view(records)
        nodes, _ = node_map(view, "t")
        self.assertEqual(nodes["c"]["start_ms"], 15)
        self.assertEqual(nodes["c"]["end_ms"], 40)  # 原始时间未被裁剪
        self.assertEqual(nodes["p"]["self_time_ms"], 5.0)  # 20-10 - (20-15)
        self.assertTrue(
            any("out_of_parent_range" in a for a in nodes["c"]["anomalies"])
        )
        self.assertTrue(
            any("child_out_of_range" in a for a in nodes["p"]["anomalies"])
        )

    def test_zero_duration_span_displayed(self):
        records = [rec("t", "root", None, "root", 5, 5)]
        view = build_view(records)
        nodes, trace = node_map(view, "t")
        self.assertEqual(trace["span_count"], 1)
        self.assertEqual(nodes["root"]["duration_ms"], 0.0)
        self.assertEqual(nodes["root"]["self_time_ms"], 0.0)
        self.assertTrue(
            any("zero_duration" in a for a in nodes["root"]["anomalies"])
        )

    def test_missing_parent_is_orphan_no_fabrication(self):
        records = [
            rec("t", "ghost-child", "missing-parent", "orphan", 0, 10),
            rec("t", "root", None, "root", 0, 5),
        ]
        view = build_view(records)
        nodes, trace = node_map(view, "t")
        self.assertEqual([r["span_id"] for r in trace["roots"]], ["root"])
        self.assertEqual([o["span_id"] for o in trace["orphans"]], ["ghost-child"])
        self.assertTrue(nodes["ghost-child"]["is_orphan"])
        # 绝不能虚构出名为 missing-parent 的节点
        self.assertNotIn("missing-parent", nodes)


class TestValidation(unittest.TestCase):
    def test_empty_input_is_empty(self):
        view = build_view(validate_records([]))
        self.assertTrue(view["empty"])
        self.assertEqual(view["traces"], [])
        self.assertEqual(view["total_spans"], 0)

    def test_parse_payload_shapes(self):
        self.assertEqual(parse_payload([]), [])
        self.assertEqual(parse_payload({"records": [1, 2]}), [1, 2])
        with self.assertRaises(TraceValidationError):
            parse_payload({"nope": []})

    def test_identical_duplicates_merged(self):
        r = rec("t", "s1", None, "x", 0, 1)
        out = validate_records([r, dict(r), dict(r)])
        self.assertEqual(len(out), 1)

    def test_same_id_conflicting_records_rejected(self):
        a = rec("t", "s1", None, "x", 0, 10)
        b = rec("t", "s1", None, "x", 0, 11)  # end 不同 => 内容冲突
        with self.assertRaisesRegex(TraceValidationError, "冲突"):
            validate_records([a, b])

    def test_same_id_in_different_traces_is_fine(self):
        # 跨 trace 同 span_id 合法且互不干扰
        out = validate_records([
            rec("t1", "s1", None, "x", 0, 1),
            rec("t2", "s1", None, "y", 0, 2),
        ])
        self.assertEqual(len(out), 2)

    def test_cycle_rejected(self):
        with self.assertRaisesRegex(TraceValidationError, "循环"):
            validate_records([
                rec("t", "a", "b", "a", 0, 1),
                rec("t", "b", "a", "b", 0, 1),
            ])

    def test_self_cycle_rejected(self):
        with self.assertRaisesRegex(TraceValidationError, "循环"):
            validate_records([rec("t", "a", "a", "a", 0, 1)])

    def test_end_before_start_rejected(self):
        with self.assertRaisesRegex(TraceValidationError, "早于"):
            validate_records([rec("t", "a", None, "a", 10, 9)])

    def test_non_finite_times_rejected(self):
        for bad in (float("inf"), float("nan"), float("-inf"), "10", None, True):
            with self.subTest(bad=bad):
                with self.assertRaises(TraceValidationError):
                    validate_records([rec("t", "a", None, "a", bad, 10)])

    def test_missing_field_rejected(self):
        good = rec("t", "a", None, "a", 0, 1)
        for field_name in ("trace_id", "span_id", "parent_span_id", "name",
                           "start_ms", "end_ms"):
            bad = dict(good)
            del bad[field_name]
            with self.subTest(field=field_name):
                with self.assertRaises(TraceValidationError):
                    validate_records([bad])

    def test_missing_parent_is_not_a_cycle(self):
        # 缺失父记录不能被误判成环
        out = validate_records([rec("t", "a", "gone", "a", 0, 1)])
        self.assertEqual(len(out), 1)


class TestCrossTraceIsolation(unittest.TestCase):
    def test_same_span_ids_not_chained(self):
        records = [
            rec("t1", "root", None, "r1", 0, 100),
            rec("t1", "leaf", "root", "L", 10, 20),
            rec("t2", "root", None, "r2", 0, 50),
            rec("t2", "leaf", "root", "L2", 5, 6),
        ]
        view = build_view(records)
        self.assertEqual({t["trace_id"] for t in view["traces"]}, {"t1", "t2"})
        n1, t1 = node_map(view, "t1")
        n2, t2 = node_map(view, "t2")
        self.assertEqual(n1["leaf"]["self_time_ms"], 10.0)
        self.assertEqual(n2["leaf"]["self_time_ms"], 1.0)
        self.assertEqual(n1["root"]["self_time_ms"], 90.0)
        self.assertEqual(n2["root"]["self_time_ms"], 49.0)
        # t2 的 leaf 不能挂到 t1 的 root 下
        self.assertEqual(
            [c["span_id"] for c in t1["roots"][0]["children"]], ["leaf"]
        )

    def test_parent_id_resolves_only_within_trace(self):
        # t2 中 child 的 parent 是 "root"，t1 也有 root；必须挂到 t2 自己的 root
        records = [
            rec("t1", "root", None, "r1", 0, 10),
            rec("t2", "root", None, "r2", 0, 10),
            rec("t2", "child", "root", "c", 1, 2),
        ]
        view = build_view(records)
        _, t2 = node_map(view, "t2")
        self.assertEqual(
            [c["span_id"] for c in t2["roots"][0]["children"]], ["child"]
        )
        # t1 的 root 没有孩子
        _, t1 = node_map(view, "t1")
        self.assertEqual(t1["roots"][0]["children"], [])


class TestCorrections(unittest.TestCase):
    def setUp(self):
        self.studio = Studio()
        self.studio.load({
            "records": [
                rec("t", "root", None, "root", 0, 100),
                rec("t", "a", "root", "a", 10, 40),
            ]
        })
        self.original_snapshot = copy.deepcopy(self.studio.original)

    def test_correction_updates_tree_and_self_time(self):
        # 把 a 从 10-40 改成 10-90：root 自身耗时应从 70 变为 20
        self.studio.apply_correction("t", "a", new_end_ms=90)
        view = self.studio.analyze()
        nodes, _ = node_map(view, "t")
        self.assertEqual(nodes["a"]["duration_ms"], 80.0)
        self.assertEqual(nodes["root"]["self_time_ms"], 20.0)

    def test_correction_reparent(self):
        # 新增 b，然后把 a 的父改成 b
        self.studio.load({
            "records": [
                rec("t", "root", None, "root", 0, 100),
                rec("t", "a", "root", "a", 10, 20),
                rec("t", "b", "root", "b", 0, 50),
            ]
        })
        self.studio.apply_correction("t", "a", new_parent_span_id="b")
        view = self.studio.analyze()
        nodes, trace = node_map(view, "t")
        self.assertEqual(nodes["a"]["parent_span_id"], "b")
        self.assertEqual(
            [c["span_id"] for c in trace["roots"][0]["children"]], ["b"]
        )
        # b 的自身耗时 = 50 - a 在 b 范围内的覆盖 10 = 40
        self.assertEqual(nodes["b"]["self_time_ms"], 40.0)
        # root 自身 = 100 - b 的覆盖 50 = 50
        self.assertEqual(nodes["root"]["self_time_ms"], 50.0)

    def test_reparent_to_root(self):
        self.studio.apply_correction("t", "a", new_parent_span_id=None)
        view = self.studio.analyze()
        nodes, trace = node_map(view, "t")
        self.assertIsNone(nodes["a"]["parent_span_id"])
        self.assertEqual({r["span_id"] for r in trace["roots"]}, {"root", "a"})

    def test_undo_restores_previous_state(self):
        self.studio.apply_correction("t", "a", new_end_ms=90)
        undone = self.studio.undo()
        self.assertIsNotNone(undone)
        view = self.studio.analyze()
        nodes, _ = node_map(view, "t")
        self.assertEqual(nodes["a"]["end_ms"], 40)
        self.assertEqual(nodes["root"]["self_time_ms"], 70.0)
        self.assertIsNone(self.studio.undo())  # 再撤销返回 None

    def test_multiple_corrections_undo_lifo(self):
        self.studio.apply_correction("t", "a", new_start_ms=5)
        self.studio.apply_correction("t", "a", new_end_ms=80)
        nodes, _ = node_map(self.studio.analyze(), "t")
        self.assertEqual((nodes["a"]["start_ms"], nodes["a"]["end_ms"]), (5, 80))
        self.studio.undo()
        nodes, _ = node_map(self.studio.analyze(), "t")
        self.assertEqual((nodes["a"]["start_ms"], nodes["a"]["end_ms"]), (5, 40))
        self.studio.undo()
        nodes, _ = node_map(self.studio.analyze(), "t")
        self.assertEqual((nodes["a"]["start_ms"], nodes["a"]["end_ms"]), (10, 40))

    def test_invalid_correction_rejected_and_state_preserved(self):
        with self.assertRaises(TraceValidationError):
            self.studio.apply_correction("t", "a", new_end_ms=5)  # end < start
        view = self.studio.analyze()
        nodes, _ = node_map(view, "t")
        self.assertEqual(nodes["a"]["end_ms"], 40)  # 保持原状
        self.assertEqual(self.studio.corrections, [])

    def test_cyclic_correction_rejected(self):
        self.studio.load({
            "records": [
                rec("t", "root", None, "root", 0, 100),
                rec("t", "a", "root", "a", 0, 10),
                rec("t", "b", "a", "b", 1, 2),
            ]
        })
        with self.assertRaisesRegex(TraceValidationError, "循环|重新检查"):
            self.studio.apply_correction("t", "root", new_parent_span_id="b")
        nodes, _ = node_map(self.studio.analyze(), "t")
        self.assertIsNone(nodes["root"]["parent_span_id"])

    def test_cross_trace_reparent_rejected(self):
        # t2 的 a 想挂到只有 t1 才有的 span_id：必须拒绝，绝不跨 trace 串联
        self.studio.load({
            "records": [
                rec("t1", "only-in-t1", None, "x", 0, 10),
                rec("t2", "a", None, "a", 0, 10),
            ]
        })
        with self.assertRaisesRegex(TraceValidationError, "其他 trace"):
            self.studio.apply_correction("t2", "a",
                                         new_parent_span_id="only-in-t1")
        # 被拒绝后状态不变
        nodes, _ = node_map(self.studio.analyze(), "t2")
        self.assertIsNone(nodes["a"]["parent_span_id"])

    def test_original_input_never_mutated(self):
        self.studio.apply_correction("t", "a", new_end_ms=90)
        self.studio.apply_correction("t", "a", new_parent_span_id=None)
        self.assertEqual(
            [dict(r) for r in self.studio.original],
            [dict(r) for r in self.original_snapshot],
        )

    def test_export_contains_corrected_copy_and_log(self):
        self.studio.apply_correction("t", "a", new_end_ms=90)
        exported = self.studio.export()
        self.assertTrue(exported["corrected"])
        self.assertEqual(len(exported["corrections"]), 1)
        by_id = {r["span_id"]: r for r in exported["records"]}
        self.assertEqual(by_id["a"]["end_ms"], 90)
        # 导出的是副本：改导出物不影响内部状态
        exported["records"][0]["end_ms"] = -999
        nodes, _ = node_map(self.studio.analyze(), "t")
        self.assertEqual(nodes["root"]["end_ms"], 100)

    def test_rename_span_id(self):
        self.studio.apply_correction("t", "a", new_span_id="a-renamed")
        view = self.studio.analyze()
        nodes, _ = node_map(view, "t")
        self.assertIn("a-renamed", nodes)
        self.assertNotIn("a", nodes)

    def test_failed_import_preserves_previous_work(self):
        # 当前已有有效数据，导入非法批次必须整体失败且旧数据保留
        before = self.studio.analyze()
        with self.assertRaises(TraceValidationError):
            self.studio.load([rec("t", "x", None, "x", 5, 1)])  # end<start
        after = self.studio.analyze()
        self.assertEqual(after["total_spans"], before["total_spans"])
        nodes, _ = node_map(after, "t")
        self.assertIn("root", nodes)


class TestSampleData(unittest.TestCase):
    def test_sample_loads_and_has_expected_structure(self):
        studio = Studio()
        count = studio.load(samples.sample_payload())
        self.assertEqual(count, 10)
        view = studio.analyze()
        # 两个 trace，且相同 span_id（root / db-query）共存
        self.assertEqual({t["trace_id"] for t in view["traces"]},
                         {samples.TRACE_A, samples.TRACE_B})
        nodes_a, trace_a = node_map(view, samples.TRACE_A)
        # 并行 db-query(30-60) 与 http-call(40-80) => root 自身耗时
        # root 0-100，直接子：
        # auth[10,20], db-query[30,60], http-call[40,80], charge[85,95],
        # cache-marker[95,95](零时长) => 并集 [10,20]U[30,80]U[85,95]
        # = 10 + 50 + 10 = 70；自身 = 30
        self.assertEqual(nodes_a["root"]["self_time_ms"], 30.0)
        # auth 的子 token-parse 越界：auth 自身 = 10 - clip(15,20)=5
        self.assertEqual(nodes_a["auth"]["self_time_ms"], 5.0)
        self.assertEqual(nodes_a["token-parse"]["duration_ms"], 10.0)
        # 孤立跨度存在且没有虚构父节点
        self.assertEqual([o["span_id"] for o in trace_a["orphans"]], ["bg-retry"])
        self.assertNotIn("missing-gateway", nodes_a)
        # 错误记录归属
        self.assertTrue(nodes_a["charge"]["is_error"])
        self.assertEqual(trace_a["error_count"], 1)


if __name__ == "__main__":
    unittest.main()
