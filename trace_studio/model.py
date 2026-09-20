"""核心数据模型与问题码。

设计原则：
- ``RawSpan`` 是不可变值对象，``raw`` 保存用户导入时的原始字典，永远不被修改。
- 修正（见 :mod:`trace_studio.corrections`）永远基于原始记录派生新副本。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# 问题码
# ---------------------------------------------------------------------------

# 致命问题：整批导入被拒绝（保留此前有效数据）
ERR_BAD_JSON = "bad_json"
ERR_NOT_LIST = "not_list"
ERR_RECORD_NOT_OBJECT = "record_not_object"
ERR_MISSING_FIELD = "missing_field"
ERR_BAD_TYPE = "bad_type"
ERR_END_BEFORE_START = "end_before_start"
ERR_NON_FINITE_TIME = "non_finite_time"
ERR_CONFLICT = "conflict"
ERR_CYCLE = "cycle"
ERR_UNKNOWN_SPAN = "unknown_span"

# 非致命异常：记录被保留，在树中明确标注
ANOM_ORPHAN = "orphan"                 # 父记录在同一追踪内不存在
ANOM_OUT_OF_RANGE = "out_of_range"     # 子跨度超出父范围
ANOM_ZERO_DURATION = "zero_duration"   # 零时长跨度（仅提示，便于展示）


@dataclass(frozen=True)
class Issue:
    """一条导入期问题或结构异常。"""

    code: str
    message: str
    trace_id: str | None = None
    span_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "trace_id": self.trace_id,
            "span_id": self.span_id,
        }


@dataclass(frozen=True)
class RawSpan:
    """一条经过校验的跨度记录。

    ``parent_id`` 为 ``None`` 表示根跨度（无父）。
    ``raw`` 是原始 JSON 对象的逐字段副本，用于“查看原始记录”。
    """

    trace_id: str
    span_id: str
    parent_id: str | None
    name: str
    start_ms: float
    end_ms: float
    has_error: bool
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def duration_ms(self) -> float:
        return self.end_ms - self.start_ms

    def to_record(self) -> dict[str, Any]:
        """导出为可重新导入的规范记录（含原始额外字段）。"""
        record = dict(self.raw)
        record.update(
            {
                "traceId": self.trace_id,
                "spanId": self.span_id,
                "parentSpanId": self.parent_id,
                "name": self.name,
                "startMs": self.start_ms,
                "endMs": self.end_ms,
            }
        )
        return record


def span_key(trace_id: str, span_id: str) -> tuple[str, str]:
    """跨度在多追踪空间内的唯一键。"""
    return (trace_id, span_id)
