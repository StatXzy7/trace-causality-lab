"""内置样例：刻意覆盖并行子调用、缺失父记录、异常与跨 trace 同 id 等情况。"""

from __future__ import annotations

from typing import Any

TRACE_A = "trace-parallel-demo"
TRACE_B = "trace-reuses-span-ids"


def sample_payload() -> dict[str, Any]:
    """返回内置样例输入（每次都是全新对象，调用方可随意修改）。"""
    return {
        "records": [
            # --- Trace A：并行重叠子调用 + 缺失父记录 + 各类异常 -----------
            {
                "trace_id": TRACE_A,
                "span_id": "root",
                "parent_span_id": None,
                "name": "POST /checkout 总入口",
                "start_ms": 0,
                "end_ms": 100,
            },
            {
                "trace_id": TRACE_A,
                "span_id": "auth",
                "parent_span_id": "root",
                "name": "鉴权 authenticate",
                "start_ms": 10,
                "end_ms": 20,
            },
            {
                # 超出父范围（25 > auth.end=20）：保留原始时间并提示
                "trace_id": TRACE_A,
                "span_id": "token-parse",
                "parent_span_id": "auth",
                "name": "解析 token",
                "start_ms": 15,
                "end_ms": 25,
            },
            {
                # 与 http-call 并行且重叠：30-60 与 40-80
                "trace_id": TRACE_A,
                "span_id": "db-query",
                "parent_span_id": "root",
                "name": "并行：查询订单库",
                "start_ms": 30,
                "end_ms": 60,
            },
            {
                "trace_id": TRACE_A,
                "span_id": "http-call",
                "parent_span_id": "root",
                "name": "并行：调用库存服务",
                "start_ms": 40,
                "end_ms": 80,
            },
            {
                # 错误记录：支付失败
                "trace_id": TRACE_A,
                "span_id": "charge",
                "parent_span_id": "root",
                "name": "扣款 charge-card",
                "start_ms": 85,
                "end_ms": 95,
                "is_error": True,
                "error": "card declined: insufficient funds",
            },
            {
                # 零时长跨度：也要能展示
                "trace_id": TRACE_A,
                "span_id": "cache-marker",
                "parent_span_id": "root",
                "name": "缓存命中标记（零时长）",
                "start_ms": 95,
                "end_ms": 95,
            },
            {
                # 缺失父记录 missing-gateway：标为孤立跨度，不虚构父节点
                "trace_id": TRACE_A,
                "span_id": "bg-retry",
                "parent_span_id": "missing-gateway",
                "name": "后台重试（父记录缺失）",
                "start_ms": 25,
                "end_ms": 70,
            },
            # --- Trace B：与 Trace A 使用相同 span_id，验证跨 trace 隔离 ---
            {
                "trace_id": TRACE_B,
                "span_id": "root",
                "parent_span_id": None,
                "name": "GET /health 总入口",
                "start_ms": 0,
                "end_ms": 10,
            },
            {
                "trace_id": TRACE_B,
                "span_id": "db-query",
                "parent_span_id": "root",
                "name": "健康检查查询（与 A 中 db-query 同名 id 但无关）",
                "start_ms": 2,
                "end_ms": 8,
            },
        ]
    }
