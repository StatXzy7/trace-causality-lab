"""调用树恢复、异常标注与自身耗时计算。

核心规则（对应需求）：

* 按 ``traceId`` 分组，**不同追踪即使 spanId 相同也绝不串联**。
* 父记录缺失 → 标记为 ``orphan``（孤立跨度），不虚构父节点；该跨度作为
  孤立根之一在 ``orphans`` 分组展示。
* 子跨度时间区间与父范围求交后计算覆盖；超出父范围的部分保留原始时间，
  并标记 ``out_of_range`` 提示异常（覆盖只算父范围内的部分）。
* 自身耗时 = 父跨度时长 − 直接子跨度（裁剪到父范围内）的**并集覆盖**时间。
  并行重叠的子调用只扣一次，不重复扣减；零时长子跨度对覆盖无贡献。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .model import ANOM_ORPHAN, ANOM_OUT_OF_RANGE, ANOM_ZERO_DURATION, RawSpan


@dataclass(frozen=True)
class Interval:
    """闭开区间 [start, end)，end <= start 视为空区间。"""

    start: float
    end: float

    @property
    def length(self) -> float:
        return max(0.0, self.end - self.start)


def union_length(intervals: list[Interval]) -> float:
    """多个区间的并集长度 —— 重叠部分只计一次。"""
    valid = [iv for iv in intervals if iv.end > iv.start]
    if not valid:
        return 0.0
    valid.sort(key=lambda iv: iv.start)
    total = 0.0
    cur_start = valid[0].start
    cur_end = valid[0].end
    for iv in valid[1:]:
        if iv.start <= cur_end:
            if iv.end > cur_end:
                cur_end = iv.end
        else:
            total += cur_end - cur_start
            cur_start, cur_end = iv.start, iv.end
    total += cur_end - cur_start
    return total


def clip(interval: Interval, bounds: Interval) -> Interval:
    """把区间裁剪到 bounds 内（求交）。"""
    return Interval(
        start=max(interval.start, bounds.start),
        end=min(interval.end, bounds.end),
    )


@dataclass
class TreeNode:
    span: RawSpan
    children: list["TreeNode"] = field(default_factory=list)
    anomalies: list[str] = field(default_factory=list)
    # 直接子跨度在父范围内的并集覆盖（毫秒）
    child_coverage_ms: float = 0.0
    # 父时长扣除并集覆盖后的自身耗时（永不小于 0）
    self_ms: float = 0.0
    # 子跨度超出父范围的总提示量（原始时间保留）
    overflow_ms: float = 0.0

    @property
    def is_orphan_root(self) -> bool:
        return ANOM_ORPHAN in self.anomalies and self.span.parent_id is not None

    def to_dict(self) -> dict[str, Any]:
        span = self.span
        return {
            "traceId": span.trace_id,
            "spanId": span.span_id,
            "parentSpanId": span.parent_id,
            "name": span.name,
            "startMs": span.start_ms,
            "endMs": span.end_ms,
            "durationMs": span.duration_ms,
            "hasError": span.has_error,
            "anomalies": list(self.anomalies),
            "childCoverageMs": round(self.child_coverage_ms, 6),
            "selfMs": round(self.self_ms, 6),
            "overflowMs": round(self.overflow_ms, 6),
            "raw": span.raw,
            "children": [child.to_dict() for child in self.children],
        }


@dataclass(frozen=True)
class TraceTree:
    trace_id: str
    roots: list[TreeNode]
    orphans: list[TreeNode]
    span_count: int
    error_count: int
    start_ms: float
    end_ms: float
    anomalies: list[dict[str, str]]

    @property
    def duration_ms(self) -> float:
        return self.end_ms - self.start_ms

    def to_dict(self) -> dict[str, Any]:
        return {
            "traceId": self.trace_id,
            "spanCount": self.span_count,
            "errorCount": self.error_count,
            "startMs": self.start_ms,
            "endMs": self.end_ms,
            "durationMs": self.duration_ms,
            "anomalies": list(self.anomalies),
            "roots": [node.to_dict() for node in self.roots],
            "orphans": [node.to_dict() for node in self.orphans],
        }


def _sort_children(nodes: list[TreeNode]) -> None:
    """子调用按开始时间、再按编号稳定排序，并递归处理。"""
    nodes.sort(key=lambda node: (node.span.start_ms, node.span.span_id))
    for node in nodes:
        _sort_children(node.children)


def _build_trace(trace_id: str, spans: list[RawSpan]) -> TraceTree:
    members: dict[str, RawSpan] = {span.span_id: span for span in spans}
    nodes: dict[str, TreeNode] = {span.span_id: TreeNode(span=span) for span in spans}

    root_nodes: list[TreeNode] = []
    orphan_nodes: list[TreeNode] = []
    anomaly_records: list[dict[str, str]] = []

    # 1) 连边：判定根 / 孤立
    for span in spans:
        node = nodes[span.span_id]
        if span.duration_ms == 0:
            node.anomalies.append(ANOM_ZERO_DURATION)
        if span.parent_id is None:
            root_nodes.append(node)
            continue
        parent = members.get(span.parent_id)
        if parent is None:
            # 缺失父记录：保留跨度、标记孤立、绝不虚构父节点
            node.anomalies.append(ANOM_ORPHAN)
            orphan_nodes.append(node)
            anomaly_records.append(
                {
                    "code": ANOM_ORPHAN,
                    "traceId": trace_id,
                    "spanId": span.span_id,
                    "missingParentId": span.parent_id,
                    "message": f"跨度 {span.name}({span.span_id}) 的父记录 "
                    f"{span.parent_id} 在追踪 {trace_id} 中缺失",
                }
            )
        else:
            nodes[parent.span_id].children.append(node)

    # 2) 范围异常与覆盖耗时
    for span in spans:
        node = nodes[span.span_id]
        parent_bounds = Interval(span.start_ms, span.end_ms)
        clipped_child_intervals: list[Interval] = []
        overflow_total = 0.0
        for child in node.children:
            child_interval = Interval(child.span.start_ms, child.span.end_ms)
            clipped = clip(child_interval, parent_bounds)
            clipped_child_intervals.append(clipped)
            overflow = child_interval.length - clipped.length
            if overflow > 0:
                overflow_total += overflow
                if ANOM_OUT_OF_RANGE not in child.anomalies:
                    child.anomalies.append(ANOM_OUT_OF_RANGE)
                anomaly_records.append(
                    {
                        "code": ANOM_OUT_OF_RANGE,
                        "traceId": trace_id,
                        "spanId": child.span.span_id,
                        "parentSpanId": span.span_id,
                        "message": f"子跨度 {child.span.name}({child.span.span_id}) "
                        f"[{child.span.start_ms}, {child.span.end_ms}] 超出父跨度 "
                        f"{span.name}({span.span_id}) [{span.start_ms}, {span.end_ms}] "
                        f"范围 {overflow:g}ms（原始时间已保留）",
                    }
                )
        coverage = union_length(clipped_child_intervals)
        node.child_coverage_ms = coverage
        node.self_ms = max(0.0, span.duration_ms - coverage)
        node.overflow_ms = overflow_total

    _sort_children(root_nodes)
    _sort_children(orphan_nodes)

    start_ms = min((span.start_ms for span in spans), default=0.0)
    end_ms = max((span.end_ms for span in spans), default=0.0)
    error_count = sum(1 for span in spans if span.has_error)

    anomaly_records.sort(key=lambda item: (item["code"], item["spanId"]))
    return TraceTree(
        trace_id=trace_id,
        roots=root_nodes,
        orphans=orphan_nodes,
        span_count=len(spans),
        error_count=error_count,
        start_ms=start_ms,
        end_ms=end_ms,
        anomalies=anomaly_records,
    )


def build_forest(spans: list[RawSpan]) -> list[TraceTree]:
    """按追踪分组恢复调用林。空输入返回空列表（页面显式显示为空）。"""
    grouped: dict[str, list[RawSpan]] = {}
    for span in spans:
        grouped.setdefault(span.trace_id, []).append(span)
    return [_build_trace(trace_id, grouped[trace_id]) for trace_id in sorted(grouped)]
