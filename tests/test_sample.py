"""内置样例的结构性断言：并行覆盖、孤立、零时长、跨追踪同编号。"""

from __future__ import annotations

import unittest

from trace_studio.parser import parse_records
from trace_studio.sample import sample_records
from trace_studio.tree import build_forest


class SampleTests(unittest.TestCase):
    def setUp(self):
        result = parse_records(sample_records())
        self.assertTrue(result.ok, result.issues)
        self.forest = build_forest(result.spans)
        self.by_trace = {tree.trace_id: tree for tree in self.forest}

    def _index(self, trace_id):
        nodes = {}

        def walk(items):
            for node in items:
                nodes[node.span.span_id] = node
                walk(node.children)

        tree = self.by_trace[trace_id]
        walk(tree.roots)
        walk(tree.orphans)
        return nodes

    def test_two_traces_with_reused_span_ids(self):
        self.assertEqual({"T-1000", "T-2000"}, set(self.by_trace))
        self.assertEqual(
            self.by_trace["T-1000"].roots[0].span.span_id,
            self.by_trace["T-2000"].roots[0].span.span_id,
        )

    def test_t1000_parallel_children_union_coverage(self):
        # r1 [0,100)：a1[10,30) + p1[35,90) + e1[90,100) → 并集 85，自身 15
        r1 = self._index("T-1000")["r1"]
        self.assertEqual(85.0, r1.child_coverage_ms)
        self.assertEqual(15.0, r1.self_ms)

    def test_t1000_nested_parallel_overlap(self):
        # p1 [35,90)：w1[40,70) 与 w2[50,80) 重叠 20ms
        # 朴素相加 30+30=60；并集 [40,80)=40；零时长 w3 无贡献 → 自身 15
        index = self._index("T-1000")
        p1 = index["p1"]
        self.assertEqual(40.0, p1.child_coverage_ms)
        self.assertEqual(15.0, p1.self_ms)
        self.assertEqual(
            ["w1", "w2", "w3"], [c.span.span_id for c in p1.children]
        )
        self.assertEqual(0.0, index["w3"].span.duration_ms)

    def test_t1000_error_span_present(self):
        e1 = self._index("T-1000")["e1"]
        self.assertTrue(e1.span.has_error)
        self.assertEqual(1, self.by_trace["T-1000"].error_count)

    def test_t1000_orphan_missing_parent(self):
        tree = self.by_trace["T-1000"]
        orphan_ids = {n.span.span_id for n in tree.orphans}
        self.assertEqual({"x1"}, orphan_ids)
        index = self._index("T-1000")
        self.assertNotIn("ghost", index)

    def test_t2000_out_of_range_child_preserved(self):
        index = self._index("T-2000")
        c1 = index["c1"]
        self.assertEqual((11.0, 12.0), (c1.span.start_ms, c1.span.end_ms))
        self.assertIn("out_of_range", c1.anomalies)
        r1 = index["r1"]
        # a1[2,8) 覆盖 6；c1 与 [0,10) 无交集 → r1 自身 4，溢出 1
        self.assertEqual(6.0, r1.child_coverage_ms)
        self.assertEqual(4.0, r1.self_ms)
        self.assertEqual(1.0, r1.overflow_ms)

    def test_t2000_does_not_borrow_t1000_children(self):
        t2000 = self.by_trace["T-2000"]
        r1 = t2000.roots[0]
        self.assertEqual({"a1", "c1"},
                         {c.span.span_id for c in r1.children})


if __name__ == "__main__":
    unittest.main()
