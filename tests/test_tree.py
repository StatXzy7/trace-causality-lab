"""树恢复与自身耗时测试 —— 断言具体调用关系与耗时数值。"""

from __future__ import annotations

import unittest

from trace_studio.model import (
    ANOM_ORPHAN,
    ANOM_OUT_OF_RANGE,
    ANOM_ZERO_DURATION,
)
from trace_studio.parser import parse_records
from trace_studio.tree import Interval, build_forest, union_length


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


def forest_of(records):
    result = parse_records(records)
    assert result.ok, result.issues
    return build_forest(result.spans)


def node_map(tree):
    index = {}

    def walk(nodes):
        for node in nodes:
            index[node.span.span_id] = node
            walk(node.children)

    walk(tree.roots)
    walk(tree.orphans)
    return index


class UnionLengthTests(unittest.TestCase):
    def test_disjoint_intervals_sum(self):
        self.assertEqual(30.0, union_length([Interval(0, 10), Interval(20, 40)]))

    def test_overlap_counted_once(self):
        # [0,20) 与 [10,30) 并集为 [0,30) = 30，而不是 20+20=40
        self.assertEqual(30.0, union_length([Interval(0, 20), Interval(10, 30)]))

    def test_identical_intervals(self):
        self.assertEqual(10.0, union_length([Interval(0, 10), Interval(0, 10)]))

    def test_nested_intervals(self):
        self.assertEqual(20.0, union_length([Interval(0, 20), Interval(5, 10)]))

    def test_touching_intervals_merge_without_gap(self):
        self.assertEqual(20.0, union_length([Interval(0, 10), Interval(10, 20)]))

    def test_empty_and_zero_length_intervals(self):
        self.assertEqual(0.0, union_length([Interval(5, 5), Interval(8, 7)]))

    def test_three_parallel_partial_overlaps(self):
        # [0,10) [5,15) [12,25) -> [0,25)
        intervals = [Interval(0, 10), Interval(5, 15), Interval(12, 25)]
        self.assertEqual(25.0, union_length(intervals))


class SelfTimeTests(unittest.TestCase):
    def test_leaf_self_time_equals_duration(self):
        forest = forest_of([span("t", "r", None, "root", 0, 10)])
        root = node_map(forest[0])["r"]
        self.assertEqual(10.0, root.self_ms)
        self.assertEqual(0.0, root.child_coverage_ms)

    def test_sequential_children_fully_covered(self):
        records = [
            span("t", "r", None, "root", 0, 100),
            span("t", "a", "r", "a", 0, 40),
            span("t", "b", "r", "b", 40, 100),
        ]
        root = node_map(forest_of(records)[0])["r"]
        self.assertEqual(100.0, root.child_coverage_ms)
        self.assertEqual(0.0, root.self_ms)

    def test_parallel_overlapping_children_not_double_deducted(self):
        # 父 [0,100)；两个并行子调用 [10,60) 与 [40,90)
        # 朴素相加会扣 100，实际并集覆盖 [10,90)=80 → 自身 20
        records = [
            span("t", "r", None, "root", 0, 100),
            span("t", "p1", "r", "parallel-a", 10, 60),
            span("t", "p2", "r", "parallel-b", 40, 90),
        ]
        root = node_map(forest_of(records)[0])["r"]
        self.assertEqual(80.0, root.child_coverage_ms)
        self.assertEqual(20.0, root.self_ms)
        # 关系断言：两个子调用都是 r 的直接子节点
        self.assertEqual(["p1", "p2"], [c.span.span_id for c in root.children])

    def test_three_way_parallel_fanout_union(self):
        # [0,30) [20,50) [40,80) 并集 [0,80)=80
        records = [
            span("t", "r", None, "root", 0, 100),
            span("t", "a", "r", "a", 0, 30),
            span("t", "b", "r", "b", 20, 50),
            span("t", "c", "r", "c", 40, 80),
        ]
        root = node_map(forest_of(records)[0])["r"]
        self.assertEqual(80.0, root.child_coverage_ms)
        self.assertEqual(20.0, root.self_ms)

    def test_only_direct_children_count_for_self_time(self):
        # 孙跨度不直接扣减祖父；父 p 自身耗时体现其内部嵌套
        records = [
            span("t", "r", None, "root", 0, 100),
            span("t", "p", "r", "parent", 10, 90),
            span("t", "g", "p", "grandchild", 20, 80),
        ]
        index = node_map(forest_of(records)[0])
        self.assertEqual(80.0, index["r"].child_coverage_ms)  # 只算 p [10,90)
        self.assertEqual(20.0, index["r"].self_ms)
        self.assertEqual(60.0, index["p"].child_coverage_ms)  # g [20,80)
        self.assertEqual(20.0, index["p"].self_ms)
        self.assertEqual(60.0, index["g"].self_ms)

    def test_zero_duration_child_contributes_no_coverage(self):
        records = [
            span("t", "r", None, "root", 0, 10),
            span("t", "z", "r", "zero", 5, 5),
        ]
        tree = forest_of(records)[0]
        index = node_map(tree)
        self.assertEqual(0.0, index["r"].child_coverage_ms)
        self.assertEqual(10.0, index["r"].self_ms)
        self.assertIn(ANOM_ZERO_DURATION, index["z"].anomalies)
        self.assertEqual(0.0, index["z"].self_ms)

    def test_self_time_never_negative(self):
        # 子跨度裁剪后不可能超过父时长；验证结果非负
        records = [
            span("t", "r", None, "root", 10, 20),
            span("t", "a", "r", "inside", 0, 30),
        ]
        root = node_map(forest_of(records)[0])["r"]
        self.assertEqual(10.0, root.child_coverage_ms)
        self.assertEqual(0.0, root.self_ms)


class OrphanTests(unittest.TestCase):
    def test_missing_parent_marked_orphan_without_fabricated_node(self):
        records = [
            span("t", "r", None, "root", 0, 10),
            span("t", "x", "ghost", "orphaned", 2, 8),
        ]
        tree = forest_of(records)[0]
        self.assertEqual(1, len(tree.roots))
        self.assertEqual("r", tree.roots[0].span.span_id)
        self.assertEqual(1, len(tree.orphans))
        orphan = tree.orphans[0]
        self.assertEqual("x", orphan.span.span_id)
        self.assertIn(ANOM_ORPHAN, orphan.anomalies)
        # 绝不能虚构出 ghost 节点
        index = node_map(tree)
        self.assertNotIn("ghost", index)
        # 孤立跨度自身耗时照算
        self.assertEqual(6.0, orphan.self_ms)
        codes = {a["code"] for a in tree.anomalies}
        self.assertIn(ANOM_ORPHAN, codes)

    def test_orphan_subtree_still_attaches_to_real_parent_chain(self):
        # a 缺失父 ghost，但 a 的子 b 父记录存在 → b 正常挂在 a 下
        records = [
            span("t", "a", "ghost", "orphan-parent", 0, 20),
            span("t", "b", "a", "real-child", 5, 15),
        ]
        tree = forest_of(records)[0]
        self.assertEqual(0, len(tree.roots))
        self.assertEqual(1, len(tree.orphans))
        orphan = tree.orphans[0]
        self.assertEqual("a", orphan.span.span_id)
        self.assertEqual(["b"], [c.span.span_id for c in orphan.children])


class OutOfRangeTests(unittest.TestCase):
    def test_child_outside_parent_preserves_original_time(self):
        records = [
            span("t", "r", None, "root", 0, 10),
            span("t", "late", "r", "late-child", 11, 12),
        ]
        tree = forest_of(records)[0]
        index = node_map(tree)
        child = index["late"]
        # 原始时间保留
        self.assertEqual((11.0, 12.0), (child.span.start_ms, child.span.end_ms))
        self.assertIn(ANOM_OUT_OF_RANGE, child.anomalies)
        # 覆盖只算父范围内的部分：交集为空 → 0
        self.assertEqual(0.0, index["r"].child_coverage_ms)
        self.assertEqual(10.0, index["r"].self_ms)
        self.assertEqual(1.0, index["r"].overflow_ms)

    def test_partial_overflow_clips_coverage(self):
        # 父 [10,20)，子 [15,30)：交集 [15,20)=5，超范围 10
        records = [
            span("t", "r", None, "root", 10, 20),
            span("t", "c", "r", "overflow", 15, 30),
        ]
        root = node_map(forest_of(records)[0])["r"]
        self.assertEqual(5.0, root.child_coverage_ms)
        self.assertEqual(5.0, root.self_ms)
        self.assertEqual(10.0, root.overflow_ms)
        self.assertIn(ANOM_OUT_OF_RANGE, root.children[0].anomalies)

    def test_child_completely_before_parent(self):
        records = [
            span("t", "r", None, "root", 100, 200),
            span("t", "early", "r", "early", 0, 50),
        ]
        root = node_map(forest_of(records)[0])["r"]
        self.assertEqual(0.0, root.child_coverage_ms)
        self.assertEqual(100.0, root.self_ms)
        self.assertEqual(50.0, root.overflow_ms)


class CrossTraceIsolationTests(unittest.TestCase):
    def test_same_span_ids_not_connected(self):
        records = [
            span("tA", "r", None, "A-root", 0, 100),
            span("tA", "c", "r", "A-child", 10, 20),
            span("tB", "r", None, "B-root", 0, 5),
            span("tB", "c", "r", "B-child", 1, 4),
        ]
        forest = forest_of(records)
        self.assertEqual(2, len(forest))
        ids = {tree.trace_id for tree in forest}
        self.assertEqual({"tA", "tB"}, ids)
        by_trace = {tree.trace_id: tree for tree in forest}
        a_root = by_trace["tA"].roots[0]
        b_root = by_trace["tB"].roots[0]
        self.assertEqual("A-root", a_root.span.name)
        self.assertEqual(100.0, a_root.self_ms + a_root.child_coverage_ms)
        self.assertEqual(5.0, b_root.span.duration_ms)
        # 每个追踪各一个 root、各自一个 child
        self.assertEqual(1, len(a_root.children))
        self.assertEqual(1, len(b_root.children))

    def test_parent_id_only_resolves_within_same_trace(self):
        # t2 里的 child 指向 r1，但 t2 中没有 r1 → 孤立；
        # 绝不能挂到 t1 的 r1 上
        records = [
            span("t1", "r1", None, "t1-root", 0, 100),
            span("t2", "c9", "r1", "t2-orphan", 0, 5),
        ]
        forest = forest_of(records)
        by_trace = {tree.trace_id: tree for tree in forest}
        self.assertEqual(0, len(by_trace["t2"].roots))
        # c9 属于 t2 且父缺失 → 孤立；绝不挂到 t1 的 r1 上
        self.assertEqual(1, len(by_trace["t2"].orphans))
        self.assertEqual([], by_trace["t1"].roots[0].children)

    def test_separate_trace_bounds(self):
        records = [
            span("t1", "a", None, "a", 1000, 2000),
            span("t2", "b", None, "b", 0, 10),
        ]
        by_trace = {tree.trace_id: tree for tree in forest_of(records)}
        self.assertEqual((1000.0, 2000.0),
                         (by_trace["t1"].start_ms, by_trace["t1"].end_ms))
        self.assertEqual((0.0, 10.0),
                         (by_trace["t2"].start_ms, by_trace["t2"].end_ms))


class ErrorAndStatsTests(unittest.TestCase):
    def test_error_count_and_flag(self):
        records = [
            span("t", "r", None, "root", 0, 10),
            span("t", "e", "r", "boom", 2, 8, error=True,
                 errorMessage="timeout"),
        ]
        tree = forest_of(records)[0]
        self.assertEqual(1, tree.error_count)
        child = tree.roots[0].children[0]
        self.assertTrue(child.span.has_error)
        self.assertEqual("timeout", child.span.raw["errorMessage"])

    def test_children_sorted_by_start_time(self):
        records = [
            span("t", "r", None, "root", 0, 100),
            span("t", "b", "r", "later", 50, 60),
            span("t", "a", "r", "earlier", 10, 20),
        ]
        root = forest_of(records)[0].roots[0]
        self.assertEqual(["a", "b"], [c.span.span_id for c in root.children])


if __name__ == "__main__":
    unittest.main()
