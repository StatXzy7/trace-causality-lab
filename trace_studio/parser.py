"""JSON 导入与校验。

校验分两级：

* **致命问题**（JSON 非法、类型错误、结束早于开始、非有限时间、编号冲突、
  循环父关系）→ 整批拒绝，返回空结果，由上层保留此前有效数据。
* **非致命异常**（缺失父、超出父范围、零时长）不在导入阶段判定，
  由 :mod:`trace_studio.tree` 在恢复结构时标注。

完全相同的记录（含额外字段、键序无关）合并为一条；
``(traceId, spanId)`` 相同但内容冲突则整批拒绝。
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Any

from .model import (
    ERR_BAD_JSON,
    ERR_BAD_TYPE,
    ERR_CONFLICT,
    ERR_CYCLE,
    ERR_END_BEFORE_START,
    ERR_MISSING_FIELD,
    ERR_NON_FINITE_TIME,
    ERR_NOT_LIST,
    ERR_RECORD_NOT_OBJECT,
    Issue,
    RawSpan,
)

_REQUIRED_STR_FIELDS = ("traceId", "spanId", "name")
_TIME_FIELDS = ("startMs", "endMs")


def _reject_constant(token: str) -> None:
    raise ValueError(f"非法数值常量 {token!r}（不允许 NaN / Infinity）")


@dataclass(frozen=True)
class ParseResult:
    """导入结果。

    ``ok`` 为 False 时 ``spans`` 必为空（整批拒绝），``issues`` 含致命原因。
    ``duplicates_merged`` 是被合并的完全相同重复记录条数。
    """

    ok: bool
    spans: list[RawSpan]
    issues: list[Issue]
    duplicates_merged: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "issues": [i.to_dict() for i in self.issues],
            "duplicates_merged": self.duplicates_merged,
        }


def _reject(issues: list[Issue]) -> ParseResult:
    return ParseResult(ok=False, spans=[], issues=issues)


def _is_real_number(value: Any) -> bool:
    # bool 是 int 的子类，时间字段显式不接受布尔值
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _error_flag(record: dict[str, Any], issues: list[Issue], index: int) -> bool:
    has_error = False
    if "error" in record:
        value = record["error"]
        if not isinstance(value, bool):
            issues.append(
                Issue(
                    ERR_BAD_TYPE,
                    f"第 {index} 条记录的 error 必须是布尔值",
                    span_id=_safe_str(record.get("spanId")),
                )
            )
        else:
            has_error = value
    if "isError" in record and isinstance(record["isError"], bool):
        has_error = has_error or record["isError"]
    status = record.get("status")
    if isinstance(status, str) and status.strip().lower() == "error":
        has_error = True
    return has_error


def _safe_str(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _normalize_one(record: Any, index: int) -> tuple[dict[str, Any] | None, list[Issue]]:
    """把一条原始对象规范化为可比较/可构造的字典。"""
    issues: list[Issue] = []
    if not isinstance(record, dict):
        return None, [
            Issue(ERR_RECORD_NOT_OBJECT, f"第 {index} 条记录必须是 JSON 对象")
        ]

    for field_name in _REQUIRED_STR_FIELDS:
        value = record.get(field_name)
        if not isinstance(value, str) or not value.strip():
            issues.append(
                Issue(
                    ERR_MISSING_FIELD,
                    f"第 {index} 条记录缺少非空字符串字段 {field_name}",
                    span_id=_safe_str(record.get("spanId")),
                )
            )

    times: dict[str, float] = {}
    for field_name in _TIME_FIELDS:
        value = record.get(field_name)
        if not _is_real_number(value):
            issues.append(
                Issue(
                    ERR_MISSING_FIELD if field_name not in record else ERR_BAD_TYPE,
                    f"第 {index} 条记录的 {field_name} 必须是数字",
                    span_id=_safe_str(record.get("spanId")),
                )
            )
            continue
        if not math.isfinite(value):
            issues.append(
                Issue(
                    ERR_NON_FINITE_TIME,
                    f"第 {index} 条记录的 {field_name} 不是有限数值",
                    span_id=_safe_str(record.get("spanId")),
                )
            )
        times[field_name] = float(value)

    parent_raw = record.get("parentSpanId", None)
    if parent_raw is not None:
        if not isinstance(parent_raw, str):
            issues.append(
                Issue(
                    ERR_BAD_TYPE,
                    f"第 {index} 条记录的 parentSpanId 必须是字符串或 null",
                    span_id=_safe_str(record.get("spanId")),
                )
            )
        elif not parent_raw.strip():
            parent_raw = None  # 空白父编号视为根

    if issues:
        return None, issues

    start_ms = times["startMs"]
    end_ms = times["endMs"]
    if math.isfinite(start_ms) and math.isfinite(end_ms) and end_ms < start_ms:
        issues.append(
            Issue(
                ERR_END_BEFORE_START,
                f"第 {index} 条记录结束时间 {end_ms} 早于开始时间 {start_ms}",
                trace_id=record["traceId"],
                span_id=record["spanId"],
            )
        )
        return None, issues

    has_error = _error_flag(record, issues, index)
    if issues:
        return None, issues

    # 额外字段原样保留（用于“查看原始记录”/导出）
    known = {
        "traceId",
        "spanId",
        "parentSpanId",
        "name",
        "startMs",
        "endMs",
        "error",
        "isError",
        "status",
    }
    extras = {k: v for k, v in record.items() if k not in known}

    normalized = {
        "traceId": record["traceId"],
        "spanId": record["spanId"],
        "parentSpanId": parent_raw,
        "name": record["name"],
        "startMs": start_ms,
        "endMs": end_ms,
        "has_error": has_error,
        "extras": extras,
        # 原始对象副本（额外字段的原始形态）
        "raw": dict(record),
    }
    return normalized, []


def _canonical(normalized: dict[str, Any]) -> str:
    """完全相同记录的判等依据：规范化内容（忽略键序，数字统一为浮点）。"""
    compare = {
        "traceId": normalized["traceId"],
        "spanId": normalized["spanId"],
        "parentSpanId": normalized["parentSpanId"],
        "name": normalized["name"],
        "startMs": normalized["startMs"],
        "endMs": normalized["endMs"],
        "has_error": normalized["has_error"],
        "extras": normalized["extras"],
    }
    return json.dumps(compare, sort_keys=True, ensure_ascii=False, default=str)


def _detect_cycles(spans: list[RawSpan]) -> list[Issue]:
    """在每个追踪内部检测父关系环（含自指父）。

    每个跨度至多一个父节点，因此沿父指针做着色 DFS 即可，
    迭代实现避免超长父子链导致递归深度问题。
    """
    by_trace: dict[str, dict[str, RawSpan]] = {}
    for span in spans:
        by_trace.setdefault(span.trace_id, {})[span.span_id] = span

    issues: list[Issue] = []
    for trace_id, members in by_trace.items():
        # 0 未访问 / 1 在当前路径上 / 2 已完成
        color: dict[str, int] = {sid: 0 for sid in members}
        cycle_chain: list[str] | None = None

        for start in members:
            if color[start] != 0:
                continue
            path: list[str] = []
            sid = start
            while True:
                if color[sid] == 0:
                    color[sid] = 1
                    path.append(sid)
                parent = members[sid].parent_id
                if parent is not None and parent in members:
                    parent_color = color[parent]
                    if parent_color == 1:
                        cut = path.index(parent)
                        cycle_chain = path[cut:] + [parent]
                        break
                    if parent_color == 0:
                        sid = parent
                        continue
                color[sid] = 2
                path.pop()
                if not path:
                    break
                sid = path[-1]
            if cycle_chain is not None:
                break

        if cycle_chain is not None:
            issues.append(
                Issue(
                    ERR_CYCLE,
                    f"追踪 {trace_id} 存在循环父关系: {' -> '.join(cycle_chain)}",
                    trace_id=trace_id,
                    span_id=cycle_chain[0],
                )
            )
    return issues


def parse_records(payload: Any) -> ParseResult:
    """解析已加载的 JSON 值（列表）为跨度集合。

    接受 Python 原生列表（例如已 ``json.loads`` 的结果）；
    若传入字符串则代为解析，``NaN``/``Infinity`` 一律按非法 JSON 拒绝。
    """
    if isinstance(payload, (str, bytes, bytearray)):
        try:
            payload = json.loads(
                payload.decode("utf-8") if isinstance(payload, (bytes, bytearray))
                else payload,
                parse_constant=_reject_constant,
            )
        except (ValueError, UnicodeDecodeError) as exc:
            return _reject([Issue(ERR_BAD_JSON, f"JSON 解析失败: {exc}")])

    if not isinstance(payload, list):
        return _reject(
            [Issue(ERR_NOT_LIST, "导入内容必须是跨度记录组成的 JSON 数组")]
        )

    normalized_list: list[dict[str, Any]] = []
    all_issues: list[Issue] = []
    for index, record in enumerate(payload):
        normalized, issues = _normalize_one(record, index)
        all_issues.extend(issues)
        if normalized is not None:
            normalized_list.append(normalized)

    if all_issues:
        return _reject(all_issues)

    # 合并完全相同记录；检测编号冲突
    seen_canonical: dict[str, RawSpan] = {}
    by_key: dict[tuple[str, str], RawSpan] = {}
    canonical_by_key: dict[tuple[str, str], str] = {}
    spans: list[RawSpan] = []
    duplicates = 0
    conflict_issues: list[Issue] = []

    for normalized in normalized_list:
        canonical = _canonical(normalized)
        key = (normalized["traceId"], normalized["spanId"])
        if canonical in seen_canonical:
            # 与此前某条完全一致（同编号必然同内容）→ 合并
            duplicates += 1
            continue
        if key in canonical_by_key and canonical_by_key[key] != canonical:
            conflict_issues.append(
                Issue(
                    ERR_CONFLICT,
                    f"跨度编号冲突: 追踪 {key[0]} 内 spanId={key[1]} "
                    "存在内容不同的多条记录",
                    trace_id=key[0],
                    span_id=key[1],
                )
            )
            continue
        span = RawSpan(
            trace_id=normalized["traceId"],
            span_id=normalized["spanId"],
            parent_id=normalized["parentSpanId"],
            name=normalized["name"],
            start_ms=normalized["startMs"],
            end_ms=normalized["endMs"],
            has_error=normalized["has_error"],
            raw=normalized["raw"],
        )
        seen_canonical[canonical] = span
        canonical_by_key[key] = canonical
        by_key[key] = span
        spans.append(span)

    if conflict_issues:
        return _reject(conflict_issues)

    cycle_issues = _detect_cycles(spans)
    if cycle_issues:
        return _reject(cycle_issues)

    return ParseResult(ok=True, spans=spans, issues=[], duplicates_merged=duplicates)
