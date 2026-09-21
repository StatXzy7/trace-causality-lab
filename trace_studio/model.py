"""纯领域逻辑：导入校验、调用树恢复、覆盖时间与自身耗时、修正与导出。

不依赖任何第三方库，也不感知 HTTP / 浏览器，便于用 unittest 直接验证。

关键语义（见需求）：
- span_id 只在同一 trace_id 内有效；不同 trace 即使 span_id 相同也绝不串联。
- 自身耗时 = 父跨度时长 - 直接子跨度在父范围内的**合并覆盖时间**，
  并行/重叠子跨度只扣一次。
- 父记录缺失标记为孤立跨度，不虚构父节点。
- 修正只作用于“有效工作副本”，原始导入输入永不改变；可逐条撤销、可导出修正副本。
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Any, Iterable

REQUIRED_FIELDS = ("trace_id", "span_id", "parent_span_id", "name", "start_ms", "end_ms")
_TIME_FIELDS = ("start_ms", "end_ms")
_OPTIONAL_ROOT_VALUES = (None, "")  # parent_span_id 取这些值表示根跨度
_UNSET = object()  # 修正参数的哨兵：区分“未提供”与“显式设为 null（改为根）”


class TraceValidationError(ValueError):
    """导入或修正未通过校验。message 面向用户，可直接展示。"""


@dataclass(frozen=True)
class SpanKey:
    """跨度的全局身份：trace_id + span_id 共同确定。"""

    trace_id: str
    span_id: str


# ---------------------------------------------------------------------------
# 解析与校验
# ---------------------------------------------------------------------------


def _is_finite_number(value: Any) -> bool:
    # bool 是 int 的子类，但语义上不是合法时间，显式排除。
    if isinstance(value, bool):
        return False
    if not isinstance(value, (int, float)):
        return False
    return math.isfinite(value)


def _validate_record_shape(raw: Any, index: int) -> dict[str, Any]:
    """校验单条记录的字段形状，返回规范化后的浅拷贝。"""
    where = f"第 {index} 条记录"
    if not isinstance(raw, dict):
        raise TraceValidationError(f"{where}：必须是 JSON 对象")

    for name in REQUIRED_FIELDS:
        if name not in raw:
            raise TraceValidationError(f"{where}：缺少字段 {name}")

    rec = dict(raw)
    trace_id = rec["trace_id"]
    span_id = rec["span_id"]
    parent_id = rec["parent_span_id"]
    name = rec["name"]

    if not isinstance(trace_id, (str, int)) or isinstance(trace_id, bool) or str(trace_id) == "":
        raise TraceValidationError(f"{where}：trace_id 必须是非空字符串或数字")
    if not isinstance(span_id, (str, int)) or isinstance(span_id, bool) or str(span_id) == "":
        raise TraceValidationError(f"{where}：span_id 必须是非空字符串或数字")
    if parent_id not in _OPTIONAL_ROOT_VALUES and (
        not isinstance(parent_id, (str, int)) or isinstance(parent_id, bool)
    ):
        raise TraceValidationError(f"{where}：parent_span_id 必须是字符串、数字或 null")
    if not isinstance(name, str) or name == "":
        raise TraceValidationError(f"{where}：name 必须是非空字符串")

    for field_name in _TIME_FIELDS:
        if not _is_finite_number(rec[field_name]):
            raise TraceValidationError(f"{where}：{field_name} 必须是有限数字")
    if rec["end_ms"] < rec["start_ms"]:
        raise TraceValidationError(
            f"{where}（span_id={span_id}）：end_ms({rec['end_ms']}) 早于 "
            f"start_ms({rec['start_ms']})"
        )

    rec["trace_id"] = str(trace_id)
    rec["span_id"] = str(span_id)
    rec["parent_span_id"] = None if parent_id in _OPTIONAL_ROOT_VALUES else str(parent_id)
    return rec


def _record_signature(rec: dict[str, Any]) -> str:
    """整条记录的内容签名，用于判断“完全相同的重复记录”。"""
    return json.dumps(rec, sort_keys=True, ensure_ascii=False, default=str)


def parse_payload(payload: Any) -> list[dict[str, Any]]:
    """接受 {"records": [...]} 或裸数组，取出原始记录列表（不做字段校验）。"""
    if isinstance(payload, dict) and isinstance(payload.get("records"), list):
        return list(payload["records"])
    if isinstance(payload, list):
        return list(payload)
    raise TraceValidationError('输入必须是记录数组，或形如 {"records": [...]} 的对象')


def validate_records(raw_records: Iterable[Any]) -> list[dict[str, Any]]:
    """对一批记录做完整校验并返回去重后的深拷贝列表。

    任一记录不合法都会整体拒绝（调用方应保留此前有效工作，不替换状态）。
    """
    raw_list = list(raw_records)
    normalized: list[dict[str, Any]] = []
    for i, raw in enumerate(raw_list):
        normalized.append(_validate_record_shape(raw, i))

    # 完全相同的记录合并；同 trace 同 span_id 但内容冲突则拒绝。
    seen_signatures: set[str] = set()
    by_key: dict[SpanKey, dict[str, Any]] = {}
    deduped: list[dict[str, Any]] = []
    for rec in normalized:
        sig = _record_signature(rec)
        if sig in seen_signatures:
            continue
        key = SpanKey(rec["trace_id"], rec["span_id"])
        existing = by_key.get(key)
        if existing is not None:
            if _record_signature(existing) != sig:
                raise TraceValidationError(
                    f"span_id 冲突：trace_id={key.trace_id} 下 span_id={key.span_id} "
                    "存在内容不同的多条记录，拒绝导入"
                )
            continue
        seen_signatures.add(sig)
        by_key[key] = rec
        deduped.append(rec)

    _reject_cycles(deduped)
    return deduped


def _reject_cycles(records: list[dict[str, Any]]) -> None:
    by_key = {SpanKey(r["trace_id"], r["span_id"]): r for r in records}
    for rec in records:
        visited: set[SpanKey] = set()
        cur = rec
        while True:
            key = SpanKey(cur["trace_id"], cur["span_id"])
            if key in visited:
                raise TraceValidationError(
                    f"检测到循环父关系，涉及 span_id={key.span_id}"
                    f"（trace_id={key.trace_id}），拒绝导入"
                )
            visited.add(key)
            parent_id = cur["parent_span_id"]
            if parent_id is None:
                break
            parent = by_key.get(SpanKey(cur["trace_id"], parent_id))
            if parent is None:
                break  # 缺失父记录是孤立跨度，不属于环
            cur = parent


# ---------------------------------------------------------------------------
# 覆盖时间与自身耗时
# ---------------------------------------------------------------------------


def merged_coverage(intervals: list[tuple[float, float]]) -> float:
    """多个 [start, end] 区间的并集长度。零时长区间不贡献覆盖。"""
    finite = [(s, e) for s, e in intervals if e > s]
    if not finite:
        return 0.0
    ordered = sorted(finite)
    total = 0.0
    cur_start, cur_end = ordered[0]
    for start, end in ordered[1:]:
        if start <= cur_end:
            if end > cur_end:
                cur_end = end
        else:
            total += cur_end - cur_start
            cur_start, cur_end = start, end
    total += cur_end - cur_start
    return total


def self_time(parent: dict[str, Any], children: list[dict[str, Any]]) -> float:
    """父跨度自身耗时。

    只统计直接子跨度、且裁剪到父范围内的部分；重叠时间通过并集合并不重复扣减。
    """
    p_start = parent["start_ms"]
    p_end = parent["end_ms"]
    intervals = [
        (max(p_start, child["start_ms"]), min(p_end, child["end_ms"]))
        for child in children
        if child["end_ms"] > p_start and child["start_ms"] < p_end
    ]
    return (p_end - p_start) - merged_coverage(intervals)


# ---------------------------------------------------------------------------
# 状态（原始输入 + 修正栈）
# ---------------------------------------------------------------------------


@dataclass
class Studio:
    """应用的全部内存状态。原始记录不可变，修正逐条压栈可撤销。"""

    original: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    corrections: list[dict[str, Any]] = field(default_factory=list)

    @property
    def effective_records(self) -> list[dict[str, Any]]:
        """应用全部修正后的有效记录（新对象，不修改原始输入）。"""
        working = [dict(rec) for rec in self.original]
        for change in self.corrections:
            key = SpanKey(change["trace_id"], change["span_id"])
            for i, rec in enumerate(working):
                if SpanKey(rec["trace_id"], rec["span_id"]) == key:
                    updated = dict(rec)
                    patch = change["patch"]
                    if "span_id" in patch:
                        updated["span_id"] = patch["span_id"]
                    if "parent_span_id" in patch:
                        updated["parent_span_id"] = patch["parent_span_id"]
                    if "start_ms" in patch:
                        updated["start_ms"] = patch["start_ms"]
                    if "end_ms" in patch:
                        updated["end_ms"] = patch["end_ms"]
                    working[i] = updated
                    break
        return working

    # -- 导入 ---------------------------------------------------------------

    def load(self, payload: Any) -> int:
        """校验并整体替换当前数据集，返回去重后记录数；失败时状态不变。"""
        records = validate_records(parse_payload(payload))
        self.original = tuple(json.loads(json.dumps(r, default=str)) for r in records)
        self.corrections = []
        return len(records)

    # -- 修正 ---------------------------------------------------------------

    def apply_correction(
        self,
        trace_id: str,
        span_id: str,
        *,
        new_span_id: str | None = None,
        new_parent_span_id: str | None | object = _UNSET,
        new_start_ms: float | None = None,
        new_end_ms: float | None = None,
    ) -> dict[str, Any]:
        """修正单条记录并对修正后的整集重新校验；失败抛出且状态不变。

        new_parent_span_id 传 None 表示显式改为根；不传（_UNSET）表示保持不变。
        """
        if not self.original:
            raise TraceValidationError("当前没有已导入的数据，无法修正")
        key = SpanKey(str(trace_id), str(span_id))
        current = self.effective_records
        target = next(
            (r for r in current if SpanKey(r["trace_id"], r["span_id"]) == key),
            None,
        )
        if target is None:
            raise TraceValidationError(f"未找到跨度：trace_id={trace_id}, span_id={span_id}")

        patch: dict[str, Any] = {}
        candidate = dict(target)

        if new_span_id is not None:
            if not isinstance(new_span_id, (str, int)) or isinstance(new_span_id, bool):
                raise TraceValidationError("新 span_id 必须是非空字符串或数字")
            if str(new_span_id) == "":
                raise TraceValidationError("新 span_id 不能为空")
            patch["span_id"] = str(new_span_id)
            candidate["span_id"] = str(new_span_id)

        if new_parent_span_id is not _UNSET:
            if new_parent_span_id in _OPTIONAL_ROOT_VALUES:
                patch["parent_span_id"] = None
                candidate["parent_span_id"] = None
            else:
                if not isinstance(new_parent_span_id, (str, int)) or isinstance(
                    new_parent_span_id, bool
                ):
                    raise TraceValidationError("新 parent_span_id 必须是字符串、数字或 null")
                pid = str(new_parent_span_id)
                patch["parent_span_id"] = pid
                candidate["parent_span_id"] = pid

        for field_name, value in (("start_ms", new_start_ms), ("end_ms", new_end_ms)):
            if value is not None:
                if not _is_finite_number(value):
                    raise TraceValidationError(f"新 {field_name} 必须是有限数字")
                patch[field_name] = value
                candidate[field_name] = value

        if candidate["end_ms"] < candidate["start_ms"]:
            raise TraceValidationError(
                "修正被拒绝：end_ms 早于 start_ms，已保留修正前状态"
            )

        # 不允许把父指向其他 trace 的跨度（不同 trace 绝不串联）。
        if candidate["parent_span_id"] is not None:
            exists_in_trace = any(
                r["trace_id"] == candidate["trace_id"]
                and r["span_id"] == candidate["parent_span_id"]
                and SpanKey(r["trace_id"], r["span_id"]) != key
                for r in current
            )
            if not exists_in_trace:
                # 指向本 trace 内不存在的 id 是允许的（成为孤立跨度）；
                # 但跨 trace 指向要明确拒绝。
                other_trace = any(
                    r["trace_id"] != candidate["trace_id"]
                    and r["span_id"] == candidate["parent_span_id"]
                    for r in current
                )
                if other_trace:
                    raise TraceValidationError(
                        "修正被拒绝：不能把父关系指向其他 trace 的跨度"
                    )

        if not patch:
            raise TraceValidationError("修正未包含任何字段变更")

        candidate_set = [r for r in current if SpanKey(r["trace_id"], r["span_id"]) != key]
        candidate_set.append(candidate)
        try:
            validate_records(candidate_set)  # 重新检查冲突、循环等
        except TraceValidationError as exc:
            raise TraceValidationError(f"修正后重新检查失败，已保留修正前状态：{exc}") from exc

        change = {
            "trace_id": key.trace_id,
            "span_id": key.span_id,
            "patch": patch,
        }
        self.corrections.append(change)
        return change

    def undo(self) -> dict[str, Any] | None:
        """撤销最近一次修正，返回被撤销的修正；没有修正时返回 None。"""
        if not self.corrections:
            return None
        return self.corrections.pop()

    # -- 导出 ---------------------------------------------------------------

    def export(self) -> dict[str, Any]:
        """导出修正副本：修正后的记录 + 修正日志。原始输入不包含变更。"""
        return {
            "records": self.effective_records,
            "corrections": [dict(c) for c in self.corrections],
            "corrected": bool(self.corrections),
        }

    # -- 分析 ---------------------------------------------------------------

    def analyze(self) -> dict[str, Any]:
        return build_view(self.effective_records)


# ---------------------------------------------------------------------------
# 建树与视图
# ---------------------------------------------------------------------------


def build_view(records: list[dict[str, Any]]) -> dict[str, Any]:
    """把有效记录组织成按 trace 分组的调用树视图。"""
    by_key: dict[SpanKey, dict[str, Any]] = {}
    for rec in records:
        by_key[SpanKey(rec["trace_id"], rec["span_id"])] = rec

    children: dict[SpanKey, list[dict[str, Any]]] = {}
    orphan_ids: set[SpanKey] = set()
    for rec in records:
        pid = rec["parent_span_id"]
        if pid is None:
            continue
        pkey = SpanKey(rec["trace_id"], pid)
        if pkey in by_key:
            children.setdefault(pkey, []).append(rec)
        else:
            orphan_ids.add(SpanKey(rec["trace_id"], rec["span_id"]))

    traces_out: list[dict[str, Any]] = []
    for trace_id in sorted({r["trace_id"] for r in records}):
        trace_records = [r for r in records if r["trace_id"] == trace_id]
        roots = sorted(
            (r for r in trace_records if r["parent_span_id"] is None),
            key=lambda r: (r["start_ms"], r["span_id"]),
        )
        orphans = sorted(
            (by_key[k] for k in orphan_ids if k.trace_id == trace_id),
            key=lambda r: (r["start_ms"], r["span_id"]),
        )

        nodes: dict[SpanKey, dict[str, Any]] = {}

        def build_node(rec: dict[str, Any], depth: int) -> dict[str, Any]:
            key = SpanKey(rec["trace_id"], rec["span_id"])
            if key in nodes:  # 有环时兜底；正常校验后不会发生
                return nodes[key]
            kids = sorted(
                children.get(key, []), key=lambda r: (r["start_ms"], r["span_id"])
            )
            child_nodes = [build_node(kid, depth + 1) for kid in kids]
            anomalies = _span_anomalies(rec, kids, by_key)
            nodes[key] = {
                "key": f"{rec['trace_id']}::{rec['span_id']}",
                "trace_id": rec["trace_id"],
                "span_id": rec["span_id"],
                "parent_span_id": rec["parent_span_id"],
                "name": rec["name"],
                "start_ms": rec["start_ms"],
                "end_ms": rec["end_ms"],
                "duration_ms": rec["end_ms"] - rec["start_ms"],
                "self_time_ms": self_time(rec, kids),
                "child_coverage_ms": (rec["end_ms"] - rec["start_ms"])
                - self_time(rec, kids),
                "depth": depth,
                "children": child_nodes,
                "child_count": len(kids),
                "is_orphan": any(a.startswith("orphan") for a in anomalies),
                "is_error": bool(rec.get("is_error") or rec.get("error")),
                "anomalies": anomalies,
                "raw": rec,
            }
            return nodes[key]

        root_nodes = [build_node(r, 0) for r in roots]
        orphan_nodes = [build_node(r, 0) for r in orphans]

        trace_start = min(r["start_ms"] for r in trace_records)
        trace_end = max(r["end_ms"] for r in trace_records)

        # 时间轴泳道：每个根/孤立子树占连续泳道。
        lane = 0
        for node in root_nodes + orphan_nodes:
            lane = _assign_lanes(node, lane)

        traces_out.append(
            {
                "trace_id": trace_id,
                "span_count": len(trace_records),
                "start_ms": trace_start,
                "end_ms": trace_end,
                "duration_ms": trace_end - trace_start,
                "roots": root_nodes,
                "orphans": orphan_nodes,
                "error_count": sum(
                    1 for r in trace_records if r.get("is_error") or r.get("error")
                ),
                "anomaly_count": sum(
                    len(_span_anomalies(r, children.get(SpanKey(trace_id, r["span_id"]), []), by_key))
                    for r in trace_records
                ),
            }
        )

    return {
        "traces": traces_out,
        "total_spans": len(records),
        "total_corrections": 0,
        "empty": len(records) == 0,
    }


def _assign_lanes(node: dict[str, Any], start_lane: int) -> int:
    """子树内每个节点一泳道，兄弟子树互不重叠，返回下一可用泳道。"""
    node["lane"] = start_lane
    next_lane = start_lane + 1
    for child in node["children"]:
        next_lane = _assign_lanes(child, next_lane)
    return next_lane


def _span_anomalies(
    rec: dict[str, Any],
    kids: list[dict[str, Any]],
    by_key: dict[SpanKey, dict[str, Any]],
) -> list[str]:
    anomalies: list[str] = []
    pid = rec["parent_span_id"]
    if pid is not None:
        parent = by_key.get(SpanKey(rec["trace_id"], pid))
        if parent is None:
            anomalies.append("orphan: 缺失父记录，已作为孤立跨度展示，未虚构父节点")
        else:
            if rec["start_ms"] < parent["start_ms"] or rec["end_ms"] > parent["end_ms"]:
                anomalies.append(
                    "out_of_parent_range: 子跨度超出父范围，保留原始时间未做裁剪"
                )
    if rec["end_ms"] == rec["start_ms"]:
        anomalies.append("zero_duration: 零时长跨度")
    for kid in kids:
        if kid["start_ms"] < rec["start_ms"] or kid["end_ms"] > rec["end_ms"]:
            anomalies.append("child_out_of_range: 存在直接子跨度超出本跨度范围")
            break
    return anomalies
