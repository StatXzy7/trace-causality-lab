"""会话状态：原始记录 + 修正历史 + 派生有效记录/调用林。

线程安全：HTTP 服务在单线程 ``ThreadingHTTPServer`` 之外默认串行处理请求；
这里仍用一把锁保护状态，便于以后开多线程。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any

from .corrections import (
    Correction,
    apply_correction_to_span,
    validate_correction,
)
from .model import RawSpan, span_key
from .parser import ParseResult, _detect_cycles
from .tree import TraceTree, build_forest


@dataclass(frozen=True)
class ActionResult:
    ok: bool
    issues: list  # list[Issue]
    duplicates_merged: int = 0


class TraceStore:
    """保存原始跨度与修正栈，派生当前可见状态。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._originals: dict[tuple[str, str], RawSpan] = {}
        self._original_order: list[tuple[str, str]] = []
        self._history: list[Correction] = []
        self._last_import_issues: list = []
        self._last_duplicates = 0

    # ------------------------------------------------------------------
    # 导入
    # ------------------------------------------------------------------
    def load_parse_result(self, result: ParseResult) -> ActionResult:
        """整批替换为一次成功解析的结果。

        仅当 ``result.ok`` 时替换；否则保留此前有效工作（原始记录与修正不变）。
        """
        with self._lock:
            if not result.ok:
                self._last_import_issues = list(result.issues)
                self._last_duplicates = 0
                return ActionResult(
                    ok=False,
                    issues=list(result.issues),
                )
            self._originals = {
                span_key(span.trace_id, span.span_id): span for span in result.spans
            }
            self._original_order = [
                span_key(span.trace_id, span.span_id) for span in result.spans
            ]
            self._history = []
            self._last_import_issues = []
            self._last_duplicates = result.duplicates_merged
            return ActionResult(
                ok=True,
                issues=[],
                duplicates_merged=result.duplicates_merged,
            )

    def clear(self) -> None:
        with self._lock:
            self._originals = {}
            self._original_order = []
            self._history = []
            self._last_import_issues = []
            self._last_duplicates = 0

    # ------------------------------------------------------------------
    # 修正 / 撤销
    # ------------------------------------------------------------------
    def effective_spans(self) -> list[RawSpan]:
        """原始记录应用全部修正后的当前有效集合（不修改原始对象）。"""
        with self._lock:
            latest: dict[tuple[str, str], Correction] = {}
            for correction in self._history:
                latest[(correction.trace_id, correction.span_id)] = correction
            spans: list[RawSpan] = []
            for key in self._original_order:
                original = self._originals[key]
                correction = latest.get(key)
                spans.append(
                    original
                    if correction is None
                    else apply_correction_to_span(original, correction)
                )
            return spans

    def forest(self) -> list[TraceTree]:
        return build_forest(self.effective_spans())

    def apply_correction(self, correction: Correction) -> ActionResult:
        with self._lock:
            issues = validate_correction(correction, self._originals)
            if issues:
                return ActionResult(ok=False, issues=issues)

            # 结构校验：在“应用该修正后的候选集合”上跑循环检测
            candidate = self._candidate_spans(correction)
            cycle_issues = _detect_cycles(candidate)
            if cycle_issues:
                return ActionResult(ok=False, issues=cycle_issues)

            self._history.append(correction)
            return ActionResult(ok=True, issues=[])

    def _candidate_spans(self, correction: Correction) -> list[RawSpan]:
        """应用一条候选修正但不落库，用于结构校验。"""
        latest: dict[tuple[str, str], Correction] = {}
        for item in self._history:
            latest[(item.trace_id, item.span_id)] = item
        latest[(correction.trace_id, correction.span_id)] = correction
        spans: list[RawSpan] = []
        for key in self._original_order:
            original = self._originals[key]
            item = latest.get(key)
            spans.append(
                original if item is None else apply_correction_to_span(original, item)
            )
        return spans

    def undo(self) -> Correction | None:
        """撤销最近一次修正；无修正时返回 None。"""
        with self._lock:
            if not self._history:
                return None
            return self._history.pop()

    # ------------------------------------------------------------------
    # 导出 / 状态
    # ------------------------------------------------------------------
    def export_records(self) -> list[dict[str, Any]]:
        """导出修正副本：当前有效记录（原始输入不受影响）。"""
        with self._lock:
            return [span.to_record() for span in self.effective_spans()]

    def export_bundle(self) -> dict[str, Any]:
        """导出含修正元数据的副本，便于审计每条修改。"""
        with self._lock:
            corrected_keys = {
                (item.trace_id, item.span_id) for item in self._history
            }
            records = []
            for span in self.effective_spans():
                record = span.to_record()
                if span_key(span.trace_id, span.span_id) in corrected_keys:
                    record["_correctionApplied"] = True
                records.append(record)
            return {
                "records": records,
                "corrections": [item.to_dict() for item in self._history],
            }

    def state_dict(self) -> dict[str, Any]:
        """供前端使用的完整状态。"""
        with self._lock:
            spans = self.effective_spans()
            forest = build_forest(spans)
            corrected_keys = {
                (item.trace_id, item.span_id) for item in self._history
            }
            return {
                "empty": len(spans) == 0,
                "spanCount": len(spans),
                "traceCount": len(forest),
                "errorCount": sum(tree.error_count for tree in forest),
                "orphanCount": sum(len(tree.orphans) for tree in forest),
                "duplicatesMerged": self._last_duplicates,
                "canUndo": bool(self._history),
                "correctionCount": len(self._history),
                "correctedSpanIds": [
                    {"traceId": trace_id, "spanId": span_id}
                    for trace_id, span_id in sorted(corrected_keys)
                ],
                "lastImportIssues": [issue.to_dict() for issue in self._last_import_issues],
                "originals": [
                    self._originals[key].to_record() for key in self._original_order
                ],
                "traces": [tree.to_dict() for tree in forest],
            }
