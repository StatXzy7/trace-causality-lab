"""单条记录修正与撤销。

关键不变量：
* **原始记录永不修改** —— 每次修正从原始 RawSpan 的 ``raw`` 字段派生一条新的
  effective RawSpan，历史压栈，撤销即弹栈。
* 修正后的集合必须通过与导入相同的校验（有限时间、end>=start、循环父关系）。
  校验失败则修正被拒绝，当前有效数据保持不变。
* 修正父关系时，新父必须属于同一追踪（跨追踪引用按缺失处理，不串联追踪）。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from .model import (
    ERR_BAD_TYPE,
    ERR_END_BEFORE_START,
    ERR_NON_FINITE_TIME,
    ERR_UNKNOWN_SPAN,
    Issue,
    RawSpan,
)
from .parser import _detect_cycles


@dataclass(frozen=True)
class Correction:
    """一次针对单个跨度的编辑（只允许改时间与父关系，不能改编号）。"""

    trace_id: str
    span_id: str
    start_ms: float
    end_ms: float
    parent_id: str | None
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "traceId": self.trace_id,
            "spanId": self.span_id,
            "startMs": self.start_ms,
            "endMs": self.end_ms,
            "parentSpanId": self.parent_id,
            "reason": self.reason,
        }


def validate_correction(
    correction: Correction, originals_by_key: dict[tuple[str, str], RawSpan]
) -> list[Issue]:
    """校验修正本身是否合法（不涉及整个集合的结构校验）。"""
    issues: list[Issue] = []
    key = (correction.trace_id, correction.span_id)
    if key not in originals_by_key:
        # 不能给不存在的记录加修正
        issues.append(
            Issue(
                ERR_UNKNOWN_SPAN,
                f"无法修正: 追踪 {correction.trace_id} 中不存在跨度 "
                f"{correction.span_id}",
                correction.trace_id,
                correction.span_id,
            )
        )
        return issues

    if not _finite_number(correction.start_ms) or not _finite_number(correction.end_ms):
        issues.append(
            Issue(
                ERR_NON_FINITE_TIME,
                "修正时间必须是有限数值",
                correction.trace_id,
                correction.span_id,
            )
        )
        return issues

    if correction.end_ms < correction.start_ms:
        issues.append(
            Issue(
                ERR_END_BEFORE_START,
                f"修正被拒绝: 结束时间 {correction.end_ms} 早于开始时间 "
                f"{correction.start_ms}",
                correction.trace_id,
                correction.span_id,
            )
        )
        return issues

    if correction.parent_id is not None and not isinstance(correction.parent_id, str):
        issues.append(
            Issue(
                ERR_BAD_TYPE,
                "parentSpanId 必须是字符串或 null",
                correction.trace_id,
                correction.span_id,
            )
        )
    elif isinstance(correction.parent_id, str) and not correction.parent_id.strip():
        # dataclass frozen，直接返回新对象的责任在调用方；这里只标问题
        issues.append(
            Issue(
                ERR_BAD_TYPE,
                "parentSpanId 为空字符串；如需设为根请使用 null",
                correction.trace_id,
                correction.span_id,
            )
        )
    return issues


def _finite_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def apply_correction_to_span(original: RawSpan, correction: Correction) -> RawSpan:
    """从原始记录派生修正后的新记录（不修改任何已有对象）。"""
    parent_id = correction.parent_id
    if isinstance(parent_id, str) and not parent_id.strip():
        parent_id = None
    raw = dict(original.raw)
    raw.update(
        {
            "startMs": correction.start_ms,
            "endMs": correction.end_ms,
            "parentSpanId": parent_id,
        }
    )
    return RawSpan(
        trace_id=original.trace_id,
        span_id=original.span_id,
        parent_id=parent_id,
        name=original.name,
        start_ms=float(correction.start_ms),
        end_ms=float(correction.end_ms),
        has_error=original.has_error,
        raw=raw,
    )
