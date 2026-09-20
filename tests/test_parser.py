"""导入校验测试：字段、时间、冲突、循环、重复合并、空输入。"""

from __future__ import annotations

import unittest

from trace_studio.model import (
    ERR_BAD_JSON,
    ERR_CONFLICT,
    ERR_CYCLE,
    ERR_END_BEFORE_START,
    ERR_NON_FINITE_TIME,
    ERR_NOT_LIST,
)
from trace_studio.parser import parse_records


def rec(**overrides):
    base = {
        "traceId": "t1",
        "spanId": "s1",
        "parentSpanId": None,
        "name": "root",
        "startMs": 0.0,
        "endMs": 10.0,
    }
    base.update(overrides)
    return base


class ParseValidTests(unittest.TestCase):
    def test_basic_import_returns_spans(self):
        result = parse_records([rec(), rec(spanId="s2", parentSpanId="s1",
                                           name="child", startMs=1, endMs=5)])
        self.assertTrue(result.ok, result.issues)
        self.assertEqual(2, len(result.spans))
        root, child = result.spans
        self.assertEqual("s1", root.span_id)
        self.assertIsNone(root.parent_id)
        self.assertEqual("s1", child.parent_id)

    def test_integer_times_accepted_as_floats(self):
        result = parse_records([rec(startMs=0, endMs=10)])
        self.assertTrue(result.ok)
        self.assertEqual(10.0, result.spans[0].duration_ms)

    def test_empty_list_is_ok_and_empty(self):
        result = parse_records([])
        self.assertTrue(result.ok)
        self.assertEqual([], result.spans)

    def test_raw_record_preserved_for_export(self):
        result = parse_records([rec(service="billing", region="cn-1")])
        self.assertEqual("billing", result.spans[0].raw["service"])
        exported = result.spans[0].to_record()
        self.assertEqual("billing", exported["service"])
        self.assertEqual("cn-1", exported["region"])

    def test_error_flag_variants(self):
        for flag in ({"error": True}, {"isError": True}, {"status": "ERROR"}):
            with self.subTest(flag=flag):
                result = parse_records([rec(**flag)])
                self.assertTrue(result.ok)
                self.assertTrue(result.spans[0].has_error)

    def test_whitespace_parent_treated_as_root(self):
        result = parse_records([rec(parentSpanId="   ")])
        self.assertTrue(result.ok)
        self.assertIsNone(result.spans[0].parent_id)


class DuplicateAndConflictTests(unittest.TestCase):
    def test_identical_records_merged(self):
        records = [rec(), dict(rec()), dict(rec())]  # 内容完全一致、键序相同
        result = parse_records(records)
        self.assertTrue(result.ok, result.issues)
        self.assertEqual(1, len(result.spans))
        self.assertEqual(2, result.duplicates_merged)

    def test_identical_records_different_key_order_merged(self):
        a = rec()
        b = {k: a[k] for k in reversed(list(a))}
        result = parse_records([a, b])
        self.assertTrue(result.ok)
        self.assertEqual(1, len(result.spans))
        self.assertEqual(1, result.duplicates_merged)

    def test_same_id_different_content_rejected(self):
        result = parse_records([rec(endMs=10), rec(endMs=11)])
        self.assertFalse(result.ok)
        self.assertEqual([], result.spans)
        self.assertEqual(ERR_CONFLICT, result.issues[0].code)

    def test_same_id_different_extras_rejected(self):
        result = parse_records([rec(service="a"), rec(service="b")])
        self.assertFalse(result.ok)
        self.assertEqual(ERR_CONFLICT, result.issues[0].code)

    def test_identical_duplicates_allowed_across_traces(self):
        # 不同追踪里同编号同内容不是冲突，也不是重复
        a = rec(traceId="tA")
        b = rec(traceId="tB")
        result = parse_records([a, b])
        self.assertTrue(result.ok, result.issues)
        self.assertEqual(2, len(result.spans))
        self.assertEqual(0, result.duplicates_merged)


class RejectAndPreserveTests(unittest.TestCase):
    def test_payload_must_be_list(self):
        result = parse_records({"traceId": "x"})
        self.assertFalse(result.ok)
        self.assertEqual(ERR_NOT_LIST, result.issues[0].code)

    def test_malformed_json_string_rejected(self):
        result = parse_records("[{not valid json")
        self.assertFalse(result.ok)
        self.assertEqual(ERR_BAD_JSON, result.issues[0].code)

    def test_nan_constant_rejected_at_json_level(self):
        result = parse_records('[{"traceId":"t","spanId":"s","name":"n",'
                               '"parentSpanId":null,"startMs":NaN,"endMs":1}]')
        self.assertFalse(result.ok)
        self.assertEqual(ERR_BAD_JSON, result.issues[0].code)

    def test_float_nan_value_rejected(self):
        result = parse_records([rec(startMs=float("nan"))])
        self.assertFalse(result.ok)
        codes = {i.code for i in result.issues}
        self.assertIn(ERR_NON_FINITE_TIME, codes)

    def test_infinity_value_rejected(self):
        result = parse_records([rec(endMs=float("inf"))])
        self.assertFalse(result.ok)
        self.assertIn(ERR_NON_FINITE_TIME, {i.code for i in result.issues})

    def test_end_before_start_rejected(self):
        result = parse_records([rec(startMs=10, endMs=9)])
        self.assertFalse(result.ok)
        self.assertEqual(ERR_END_BEFORE_START, result.issues[0].code)

    def test_zero_duration_allowed(self):
        result = parse_records([rec(startMs=5, endMs=5)])
        self.assertTrue(result.ok)
        self.assertEqual(0, result.spans[0].duration_ms)

    def test_missing_required_field_rejected(self):
        result = parse_records([{"spanId": "s1", "name": "n"}])
        self.assertFalse(result.ok)

    def test_boolean_time_rejected(self):
        result = parse_records([rec(startMs=True, endMs=1)])
        self.assertFalse(result.ok)

    def test_record_not_object_rejected(self):
        result = parse_records([42, "x"])
        self.assertFalse(result.ok)


class CycleTests(unittest.TestCase):
    def _three_spans(self):
        return [
            rec(spanId="a", parentSpanId="c", startMs=0, endMs=10),
            rec(spanId="b", parentSpanId="a", startMs=1, endMs=9),
            rec(spanId="c", parentSpanId="b", startMs=2, endMs=8),
        ]

    def test_three_node_cycle_rejected(self):
        result = parse_records(self._three_spans())
        self.assertFalse(result.ok)
        self.assertEqual(ERR_CYCLE, result.issues[0].code)
        self.assertEqual([], result.spans)

    def test_self_parent_cycle_rejected(self):
        result = parse_records([rec(spanId="a", parentSpanId="a")])
        self.assertFalse(result.ok)
        self.assertEqual(ERR_CYCLE, result.issues[0].code)

    def test_cycle_in_one_trace_does_not_blame_other_trace(self):
        records = self._three_spans()
        # 同编号结构出现在另一追踪但无环，必须仍按追踪独立判断
        other = [
            rec(traceId="t2", spanId="a", parentSpanId=None),
            rec(traceId="t2", spanId="b", parentSpanId="a",
                startMs=1, endMs=9),
        ]
        result = parse_records(records + other)
        self.assertFalse(result.ok)
        self.assertEqual("t1", result.issues[0].trace_id)


if __name__ == "__main__":
    unittest.main()
