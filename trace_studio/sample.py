"""内置样例。

刻意覆盖：并行重叠子调用、缺失父记录（孤立跨度）、零时长跨度、错误记录、
子跨度超出父范围，以及两个追踪复用相同 spanId（验证跨追踪隔离）。
"""

from __future__ import annotations

from typing import Any


def sample_records() -> list[dict[str, Any]]:
    return [
        # ---- 追踪 T-1000：结算管线，含并行 fan-out ----
        {
            "traceId": "T-1000",
            "spanId": "r1",
            "parentSpanId": None,
            "name": "POST /checkout",
            "startMs": 0,
            "endMs": 100,
            "service": "gateway",
        },
        {
            "traceId": "T-1000",
            "spanId": "a1",
            "parentSpanId": "r1",
            "name": "validate-cart",
            "startMs": 10,
            "endMs": 30,
            "service": "cart",
        },
        {
            "traceId": "T-1000",
            "spanId": "p1",
            "parentSpanId": "r1",
            "name": "parallel-dispatch",
            "startMs": 35,
            "endMs": 90,
            "service": "worker",
        },
        {
            "traceId": "T-1000",
            "spanId": "w1",
            "parentSpanId": "p1",
            "name": "http://inventory/hold",
            "startMs": 40,
            "endMs": 70,
            "service": "inventory",
        },
        {
            "traceId": "T-1000",
            "spanId": "w2",
            "parentSpanId": "p1",
            "name": "http://pricing/quote",
            "startMs": 50,
            "endMs": 80,
            "service": "pricing",
        },
        {
            # 零时长跨度：必须能展示
            "traceId": "T-1000",
            "spanId": "w3",
            "parentSpanId": "p1",
            "name": "cache:invalidate",
            "startMs": 85,
            "endMs": 85,
            "service": "cache",
        },
        {
            # 错误记录：支付失败
            "traceId": "T-1000",
            "spanId": "e1",
            "parentSpanId": "r1",
            "name": "charge-card",
            "startMs": 90,
            "endMs": 100,
            "error": True,
            "errorMessage": "card declined: insufficient funds",
            "service": "billing",
        },
        {
            # 父记录 ghost 在 T-1000 中缺失 → 孤立跨度，不虚构父节点
            "traceId": "T-1000",
            "spanId": "x1",
            "parentSpanId": "ghost",
            "name": "orphaned-audit",
            "startMs": 20,
            "endMs": 40,
            "service": "audit",
        },
        # ---- 追踪 T-2000：故意复用 r1 / a1 编号，验证跨追踪绝不串联 ----
        {
            "traceId": "T-2000",
            "spanId": "r1",
            "parentSpanId": None,
            "name": "isolated-job",
            "startMs": 0,
            "endMs": 10,
            "service": "cron",
        },
        {
            "traceId": "T-2000",
            "spanId": "a1",
            "parentSpanId": "r1",
            "name": "nested-other-trace",
            "startMs": 2,
            "endMs": 8,
            "service": "cron",
        },
        {
            # 子跨度超出父范围：原始时间保留并提示异常
            "traceId": "T-2000",
            "spanId": "c1",
            "parentSpanId": "r1",
            "name": "stray-child",
            "startMs": 11,
            "endMs": 12,
            "service": "cron",
        },
    ]
