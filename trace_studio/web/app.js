/* trace_studio 前端逻辑 —— 原生 JS，无第三方依赖。
 *
 * 状态完全以服务端 /api/state 为准；前端只保存：
 *   state      最近一次服务端状态
 *   selection  当前选中跨度 {traceId, spanId}
 *   views      每个追踪的时间轴视图 {scale, offsetLeftMs} 与折叠状态
 */
"use strict";

// ---------------------------------------------------------------------------
// 全局状态
// ---------------------------------------------------------------------------

let state = null;
let selection = null;
const collapsed = new Set(); // "traceId:spanId"
// 每个追踪独立的缩放视图：{scale: px/ms, winStart, winEnd}（win 为毫秒窗口）
const zoomViews = new Map();

const ROW_H = 22;
const LABEL_W = 150;
const BAR_H = 14;
const MIN_BAR_W = 3;

const $ = (id) => document.getElementById(id);

// ---------------------------------------------------------------------------
// API
// ---------------------------------------------------------------------------

async function api(path, options = {}) {
  const resp = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  return resp.json();
}

async function refreshState(nextState) {
  state = nextState || (await api("/api/state"));
  render();
}

// ---------------------------------------------------------------------------
// 消息条
// ---------------------------------------------------------------------------

function showMessages(issues, kind = "error") {
  const box = $("messages");
  for (const issue of issues || []) {
    const el = document.createElement("div");
    el.className = `msg ${kind}`;
    el.innerHTML = `<span class="code">${escapeHtml(issue.code || kind)}</span>` +
      `<span>${escapeHtml(issue.message || "")}</span>`;
    box.appendChild(el);
  }
  while (box.childNodes.length > 8) box.removeChild(box.firstChild);
}

function flash(kind, code, message) {
  showMessages([{ code, message }], kind);
}

function clearMessages() {
  $("messages").innerHTML = "";
}

// ---------------------------------------------------------------------------
// 工具
// ---------------------------------------------------------------------------

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (ch) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]
  ));
}

function fmtMs(v) {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  const n = Math.abs(v) >= 100 ? v.toFixed(1) : v.toFixed(2);
  return `${n} ms`;
}

function findTrace(traceId) {
  return state.traces.find((t) => t.traceId === traceId);
}

function findNode(traceId, spanId) {
  const trace = findTrace(traceId);
  if (!trace) return null;
  let found = null;
  const walk = (nodes) => {
    for (const node of nodes) {
      if (node.spanId === spanId) { found = node; return; }
      walk(node.children);
      if (found) return;
    }
  };
  walk(trace.roots);
  walk(trace.orphans);
  return found;
}

function findOriginal(traceId, spanId) {
  return (state.originals || []).find(
    (r) => r.traceId === traceId && r.spanId === spanId
  );
}

function isCorrected(traceId, spanId) {
  return (state.correctedSpanIds || []).some(
    (c) => c.traceId === traceId && c.spanId === spanId
  );
}

// ---------------------------------------------------------------------------
// 渲染入口
// ---------------------------------------------------------------------------

function render() {
  renderHeader();
  const empty = !state || state.empty;
  $("empty-state").classList.toggle("hidden", !empty);
  $("tree-pane").classList.toggle("hidden", empty);
  $("timeline-pane").classList.toggle("hidden", empty);
  $("detail-pane").classList.toggle("hidden", empty);
  $("btn-undo").disabled = !state || !state.canUndo;
  if (empty) return;
  renderTree();
  renderTimelines();
  renderDetail();
}

function renderHeader() {
  const el = $("header-stats");
  if (!state || state.empty) { el.innerHTML = ""; return; }
  el.innerHTML = `
    <span>追踪 <b>${state.traceCount}</b></span>
    <span>跨度 <b>${state.spanCount}</b></span>
    <span class="stat-error">错误 <b>${state.errorCount}</b></span>
    <span class="stat-orphan">孤立 <b>${state.orphanCount}</b></span>
    <span>修正 <b>${state.correctionCount}</b></span>`;
}

// ---------------------------------------------------------------------------
// 调用树
// ---------------------------------------------------------------------------

function anomalyBadges(node) {
  const parts = [];
  if (node.hasError) parts.push('<span class="badge error">err</span>');
  if (node.anomalies.includes("orphan"))
    parts.push('<span class="badge orphan">孤立</span>');
  if (node.anomalies.includes("out_of_range"))
    parts.push('<span class="badge range">超范围</span>');
  if (node.anomalies.includes("zero_duration"))
    parts.push('<span class="badge zero">0ms</span>');
  if (isCorrected(node.traceId, node.spanId))
    parts.push('<span class="badge fixed">已修正</span>');
  return parts.join("");
}

function renderTree() {
  const root = $("tree-root");
  root.innerHTML = "";
  for (const trace of state.traces) {
    const section = document.createElement("div");
    section.className = "tree-section";
    section.innerHTML =
      `<div class="trace-head"><span class="tid">${escapeHtml(trace.traceId)}</span>` +
      `<span class="tmeta">${trace.spanCount} 跨度 · ${fmtMs(trace.durationMs)} · ` +
      `${trace.errorCount} 错误</span></div>`;

    const forest = document.createElement("div");
    for (const node of trace.roots) forest.appendChild(treeNodeEl(trace, node, 0));
    section.appendChild(forest);
    root.appendChild(section);

    if (trace.orphans.length) {
      const osec = document.createElement("div");
      osec.className = "tree-section orphan";
      osec.innerHTML =
        `<div class="trace-head" style="background:#241d12">` +
        `<span class="tid" style="color:var(--warn)">孤立跨度 · ${escapeHtml(trace.traceId)}</span>` +
        `<span class="tmeta">父记录缺失，未虚构父节点（${trace.orphans.length}）</span></div>`;
      const oforest = document.createElement("div");
      for (const node of trace.orphans) oforest.appendChild(treeNodeEl(trace, node, 0, true));
      osec.appendChild(oforest);
      root.appendChild(osec);
    }
  }
}

function treeNodeEl(trace, node, depth, isOrphan = false) {
  const wrap = document.createElement("div");
  wrap.className = "tree-node";
  const key = `${trace.traceId}:${node.spanId}`;
  const hasChildren = node.children.length > 0;
  const isCollapsed = collapsed.has(key);

  const row = document.createElement("div");
  row.className = "tree-row";
  if (selection && selection.traceId === trace.traceId &&
      selection.spanId === node.spanId) row.classList.add("selected");
  row.style.paddingLeft = `${8 + depth * 14}px`;
  row.innerHTML =
    `<span class="tree-toggle">${hasChildren ? (isCollapsed ? "▶" : "▼") : "·"}</span>` +
    `<span class="tree-name" title="${escapeHtml(node.name)}">${escapeHtml(node.name)}</span>` +
    anomalyBadges(node) +
    `<span class="tree-dur">${fmtMs(node.durationMs)}</span>`;
  row.addEventListener("click", (event) => {
    if (hasChildren && event.target.classList.contains("tree-toggle")) {
      if (isCollapsed) collapsed.delete(key); else collapsed.add(key);
      renderTree();
      return;
    }
    selectNode(trace.traceId, node.spanId);
  });
  wrap.appendChild(row);

  if (hasChildren && !isCollapsed) {
    for (const child of node.children) wrap.appendChild(treeNodeEl(trace, child, depth + 1));
  }
  return wrap;
}

function selectNode(traceId, spanId) {
  selection = { traceId, spanId };
  renderTree();
  renderDetail();
  highlightTimeline();
  scrollSelectionIntoView();
}

// ---------------------------------------------------------------------------
// 时间轴
// ---------------------------------------------------------------------------

function defaultView(trace) {
  return { winStart: trace.startMs, winEnd: trace.endMs };
}

function getView(trace) {
  if (!zoomViews.has(trace.traceId)) zoomViews.set(trace.traceId, defaultView(trace));
  return zoomViews.get(trace.traceId);
}

function renderTimelines() {
  const root = $("timeline-root");
  root.innerHTML = "";
  for (const trace of state.traces) {
    root.appendChild(traceBlock(trace));
  }
  updateZoomInfo();
}

function collectLanes(trace) {
  const lanes = [];
  const place = (node, depth) => {
    while (lanes.length <= depth) lanes.push([]);
    lanes[depth].push(node);
    for (const child of node.children) place(child, depth + 1);
  };
  for (const node of trace.roots) place(node, 0);
  // 孤立子树与正常根树之间留一个空行，避免行重叠
  if (trace.orphans.length) {
    const offset = lanes.length + 1;
    for (const node of trace.orphans) place(node, offset);
  }
  return lanes;
}

function niceStep(pxPerMs, targetPx = 110) {
  const targetMs = targetPx / pxPerMs;
  const pow = Math.pow(10, Math.floor(Math.log10(targetMs)));
  const candidates = [1, 2, 2.5, 5, 10].map((m) => m * pow);
  return candidates.find((s) => s * pxPerMs >= targetPx * 0.6) || candidates[candidates.length - 1];
}

function traceBlock(trace) {
  const block = document.createElement("div");
  block.className = "trace-block";
  block.dataset.traceId = trace.traceId;

  const anomalies = trace.anomalies.length
    ? ` · <span style="color:var(--warn)">${trace.anomalies.length} 异常</span>`
    : "";
  const head = document.createElement("div");
  head.className = "trace-head";
  head.innerHTML =
    `<span class="tid">${escapeHtml(trace.traceId)}</span>` +
    `<span class="tmeta">范围 ${trace.startMs}–${trace.endMs} ms${anomalies}</span>`;
  block.appendChild(head);

  const scroll = document.createElement("div");
  scroll.className = "timeline-scroll";
  scroll.dataset.traceId = trace.traceId;

  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.classList.add("timeline-svg");
  scroll.appendChild(svg);
  block.appendChild(scroll);

  const lanes = collectLanes(trace);
  const view = getView(trace);
  const containerWidth = Math.max($("timeline-pane").clientWidth - 24, 400);
  const winMs = Math.max(view.winEnd - view.winStart, 1e-9);
  const pxPerMs = containerWidth / winMs;

  const axisH = 26;
  const innerH = lanes.length * ROW_H + 12;
  const width = containerWidth;
  const height = axisH + innerH;
  svg.setAttribute("width", width);
  svg.setAttribute("height", height);
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);

  const xOf = (ms) => (ms - view.winStart) * pxPerMs;

  drawGrid(svg, view, pxPerMs, width, axisH, height);

  lanes.forEach((lane, depth) => {
    const y = axisH + depth * ROW_H;
    for (const node of lane) drawBar(svg, trace, node, depth, xOf, y, pxPerMs, axisH);
  });

  // 交互：Ctrl/⌘ + 滚轮缩放；拖拽时间轴空白区域平移
  scroll.addEventListener("wheel", (event) => {
    if (!event.ctrlKey && !event.metaKey) return; // 普通滚轮保留给页面滚动
    event.preventDefault();
    zoomAtTrace(trace, event, scroll);
  }, { passive: false });

  scroll.addEventListener("mousedown", (event) => {
    // 在条形/菱形上按下不启动平移（保留点击选择）
    if (event.target.classList && event.target.classList.contains("tl-bar")) return;
    dragState.active = true;
    dragState.trace = trace;
    dragState.startX = event.clientX;
    dragState.moved = false;
    const v = getView(trace);
    dragState.startWin = [v.winStart, v.winEnd];
    scroll.classList.add("dragging");
    event.preventDefault();
  });

  // 记录容器宽度用于首次布局
  return block;
}

// 平移拖拽的全局状态（window 监听只在 init 绑定一次）
const dragState = { active: false, trace: null, startX: 0, moved: false, startWin: null };

function handleDragMove(event) {
  if (!dragState.active) return;
  const dx = event.clientX - dragState.startX;
  if (Math.abs(dx) > 3) dragState.moved = true;
  const v = getView(dragState.trace);
  const width = $("timeline-pane").clientWidth - 24;
  const dxMs = (-dx / Math.max(width, 1)) * (dragState.startWin[1] - dragState.startWin[0]);
  v.winStart = dragState.startWin[0] + dxMs;
  v.winEnd = dragState.startWin[1] + dxMs;
  renderTimelines();
  highlightTimeline();
}

function handleDragUp() {
  if (!dragState.active) return;
  dragState.active = false;
  document.querySelectorAll(".timeline-scroll.dragging")
    .forEach((el) => el.classList.remove("dragging"));
}

function lances_h(lanes) { return lanes.length; }

function drawGrid(svg, view, pxPerMs, width, axisH, height) {
  const g = document.createElementNS("http://www.w3.org/2000/svg", "g");
  g.setAttribute("class", "svg-grid");
  const step = niceStep(pxPerMs);
  const first = Math.ceil(view.winStart / step) * step;
  for (let t = first; t <= view.winEnd + 1e-9; t += step) {
    const x = (t - view.winStart) * pxPerMs;
    const line = document.createElementNS("http://www.w3.org/2000/svg", "line");
    line.setAttribute("x1", x); line.setAttribute("x2", x);
    line.setAttribute("y1", axisH); line.setAttribute("y2", height);
    g.appendChild(line);
    const text = document.createElementNS("http://www.w3.org/2000/svg", "text");
    text.setAttribute("x", x + 3);
    text.setAttribute("y", 14);
    text.textContent = `${trimNum(t)}ms`;
    g.appendChild(text);
  }
  svg.appendChild(g);
}

function trimNum(v) {
  return Math.abs(v - Math.round(v)) < 1e-9 ? String(Math.round(v)) : v.toFixed(2);
}

function drawBar(svg, trace, node, depth, xOf, y, pxPerMs, axisH) {
  const key = `${trace.traceId}:${node.spanId}`;
  const group = document.createElementNS("http://www.w3.org/2000/svg", "g");
  group.dataset.key = key;

  // 整行悬停/点击热区
  const hit = document.createElementNS("http://www.w3.org/2000/svg", "rect");
  hit.setAttribute("class", "tl-row-bg");
  hit.setAttribute("x", 0);
  hit.setAttribute("y", y);
  hit.setAttribute("width", "100%");
  hit.setAttribute("height", ROW_H);
  group.appendChild(hit);

  const x1 = xOf(node.startMs);
  const x2 = xOf(node.endMs);
  let w = x2 - x1;
  const isZero = node.endMs === node.startMs;
  const outOfRange = node.anomalies.includes("out_of_range");
  const hasError = node.hasError;

  if (isZero) {
    // 零时长：菱形标记，保证可见
    const cx = Math.max(4, x1);
    const cy = y + ROW_H / 2;
    const diamond = document.createElementNS("http://www.w3.org/2000/svg", "polygon");
    diamond.setAttribute("points", `${cx},${cy - 7} ${cx + 6},${cy} ${cx},${cy + 7} ${cx - 6},${cy}`);
    diamond.setAttribute("class", "tl-bar zero-dur");
    group.appendChild(diamond);
  } else {
    const shownW = Math.max(w, MIN_BAR_W);
    const bar = document.createElementNS("http://www.w3.org/2000/svg", "rect");
    let cls = "tl-bar " + (depth % 2 === 0 ? "depth-even" : "depth-odd");
    if (hasError) cls = "tl-bar has-error";
    if (outOfRange) cls += " overflow-mark";
    bar.setAttribute("class", cls);
    bar.setAttribute("rx", 2);
    bar.setAttribute("x", x1);
    bar.setAttribute("y", y + (ROW_H - BAR_H) / 2);
    bar.setAttribute("width", shownW);
    bar.setAttribute("height", BAR_H);
    group.appendChild(bar);

    if (w < MIN_BAR_W) {
      // 视觉最小宽度提示线（真实位置）
      const tick = document.createElementNS("http://www.w3.org/2000/svg", "line");
      tick.setAttribute("x1", x1); tick.setAttribute("x2", x1);
      tick.setAttribute("y1", y + 3); tick.setAttribute("y2", y + ROW_H - 3);
      tick.setAttribute("stroke", "var(--text-faint)");
      group.appendChild(tick);
    }
  }

  // 文本标签放在条形右侧；空间不足则放右侧外
  const label = document.createElementNS("http://www.w3.org/2000/svg", "text");
  label.setAttribute("class", "tl-label");
  const labelX = Math.max(x2 + 6, x1 + 6);
  label.setAttribute("x", isZero ? x1 + 10 : labelX);
  label.setAttribute("y", y + ROW_H / 2 + 3.5);
  label.textContent = `${node.name}  ${fmtMs(node.durationMs)}` +
    (hasError ? "  ✕" : "") + (outOfRange ? "  ⚠超范围" : "");
  group.appendChild(label);

  if (node.anomalies.includes("orphan")) {
    const ol = document.createElementNS("http://www.w3.org/2000/svg", "line");
    ol.setAttribute("class", "tl-orphan-line");
    ol.setAttribute("x1", 0); ol.setAttribute("x2", "100%");
    ol.setAttribute("y1", y + ROW_H - 2); ol.setAttribute("y2", y + ROW_H - 2);
    group.appendChild(ol);
  }

  group.addEventListener("click", () => {
    if (dragState.moved) { dragState.moved = false; return; }
    selectNode(trace.traceId, node.spanId);
  });
  group.addEventListener("mouseenter", (e) => showTooltip(e, trace, node));
  group.addEventListener("mousemove", moveTooltip);
  group.addEventListener("mouseleave", hideTooltip);

  svg.appendChild(group);
}

function highlightTimeline() {
  if (!selection) return;
  document.querySelectorAll("#timeline-root g[data-key]").forEach((g) => {
    const bar = g.querySelector(".tl-bar");
    if (!bar) return;
    bar.classList.toggle("selected", g.dataset.key ===
      `${selection.traceId}:${selection.spanId}`);
  });
}

function scrollSelectionIntoView() {
  if (!selection) return;
  const g = document.querySelector(
    `#timeline-root g[data-key="${cssEscape(selection.traceId)}:${cssEscape(selection.spanId)}"]`
  );
  if (g) g.closest(".timeline-scroll").scrollIntoView({ block: "center" });
}

function cssEscape(v) { return window.CSS && CSS.escape ? CSS.escape(v) : v; }

// ---------------------------------------------------------------------------
// 缩放与平移
// ---------------------------------------------------------------------------

function zoomAtTrace(trace, event, scrollEl) {
  const view = getView(trace);
  const rect = scrollEl.getBoundingClientRect();
  // 鼠标在内容宽度上对应的时间点（以内容全宽=窗口宽度近似）
  const ratio = Math.min(Math.max((event.clientX - rect.left) / rect.width, 0), 1);
  const anchor = view.winStart + ratio * (view.winEnd - view.winStart);
  const factor = event.deltaY < 0 ? 0.7 : 1.42; // 向上滚放大
  let left = anchor - (anchor - view.winStart) * factor;
  let right = anchor + (view.winEnd - anchor) * factor;

  const span = trace.endMs - trace.startMs || 1;
  const minWin = span / 500;
  const maxWin = span * 4;
  if (right - left < minWin) {
    const mid = (left + right) / 2;
    left = mid - minWin / 2; right = mid + minWin / 2;
  }
  if (right - left > maxWin) {
    const mid = (left + right) / 2;
    left = mid - maxWin / 2; right = mid + maxWin / 2;
  }
  view.winStart = left;
  view.winEnd = right;
  renderTimelines();
  highlightTimeline();
}

function updateZoomInfo() {
  if (!selection) { $("zoom-info").textContent = "Ctrl/⌘+滚轮缩放 · 拖拽平移"; return; }
}

// ---------------------------------------------------------------------------
// Tooltip
// ---------------------------------------------------------------------------

const tooltip = $("tooltip");

function showTooltip(event, trace, node) {
  const lines = [
    `<b>${escapeHtml(node.name)}</b>`,
    `trace=${escapeHtml(trace.traceId)} span=${escapeHtml(node.spanId)}`,
    `区间 [${node.startMs}, ${node.endMs}] 总 ${fmtMs(node.durationMs)}`,
    `直接子覆盖 ${fmtMs(node.childCoverageMs)} · 自身 ${fmtMs(node.selfMs)}`,
  ];
  if (node.anomalies.length)
    lines.push(`异常: ${node.anomalies.join(", ")}`);
  tooltip.innerHTML = lines.join("<br/>");
  tooltip.style.display = "block";
  moveTooltip(event);
}

function moveTooltip(event) {
  tooltip.style.left = `${event.clientX + 14}px`;
  tooltip.style.top = `${event.clientY + 14}px`;
}

function hideTooltip() {
  tooltip.style.display = "none";
}

// ---------------------------------------------------------------------------
// 详情面板
// ---------------------------------------------------------------------------

function renderDetail() {
  const empty = $("detail-empty");
  const root = $("detail-root");
  if (!selection) {
    empty.classList.remove("hidden");
    root.classList.add("hidden");
    return;
  }
  const node = findNode(selection.traceId, selection.spanId);
  if (!node) { selection = null; renderDetail(); return; }
  empty.classList.add("hidden");
  root.classList.remove("hidden");

  const original = findOriginal(node.traceId, node.spanId) || {};
  const corrected = isCorrected(node.traceId, node.spanId);
  const duration = node.durationMs;
  const selfPct = duration > 0 ? (node.selfMs / duration) * 100 : 0;
  const childPct = duration > 0 ? (node.childCoverageMs / duration) * 100 : 0;
  const overflowPct = duration > 0 ? Math.min(100, (node.overflowMs / duration) * 100) : 0;

  const anomalyHtml = node.anomalies.length
    ? `<div class="detail-section"><h3>异常提示</h3>` +
      node.anomalies.map((code) => {
        const hit = findTrace(node.traceId).anomalies.find(
          (a) => a.spanId === node.spanId && a.code === code
        );
        return `<div class="msg warn" style="padding:6px 8px">` +
          `<span class="code">${escapeHtml(code)}</span>` +
          `<span>${escapeHtml(hit ? hit.message : code)}</span></div>`;
      }).join("") + `</div>`
    : "";

  root.innerHTML = `
    <div class="detail-section">
      <h3>${node.hasError ? '<span class="badge error">错误记录</span> ' : ""}
        ${escapeHtml(node.name)}${corrected ? ' <span class="badge fixed">已修正</span>' : ""}</h3>
      <dl class="kv">
        <dt>traceId</dt><dd>${escapeHtml(node.traceId)}</dd>
        <dt>spanId</dt><dd>${escapeHtml(node.spanId)}</dd>
        <dt>parentSpanId</dt><dd>${node.parentSpanId === null ? "<i>null（根）</i>" : escapeHtml(node.parentSpanId)}</dd>
        <dt>时间区间</dt><dd>[${node.startMs}, ${node.endMs}] ms</dd>
        <dt>总时长</dt><dd>${fmtMs(duration)}</dd>
      </dl>
    </div>

    <div class="detail-section">
      <h3>耗时归属</h3>
      <div class="timing-card">
        <div class="timing-row"><span>总时长</span><span class="v">${fmtMs(duration)}</span></div>
        <div class="timing-row"><span>直接子调用并集覆盖</span><span class="v">${fmtMs(node.childCoverageMs)}</span></div>
        <div class="timing-row self-time"><span>自身耗时（并行重叠不重复扣减）</span><span class="v">${fmtMs(node.selfMs)}</span></div>
        ${node.overflowMs > 0 ? `<div class="timing-row"><span style="color:var(--warn)">子跨度超范围量（原始时间保留）</span><span class="v" style="color:var(--warn)">${fmtMs(node.overflowMs)}</span></div>` : ""}
        <div class="timing-bar" title="蓝=自身 灰=子覆盖 斜纹=超范围">
          <div class="seg-self" style="width:${selfPct}%"></div>
          <div class="seg-child" style="width:${childPct}%"></div>
          <div class="seg-overflow" style="width:${overflowPct}%"></div>
        </div>
      </div>
    </div>

    <div class="detail-section">
      <h3>直接子调用（${node.children.length}）</h3>
      ${node.children.length ? `<ul class="child-list">${node.children.map((c) => `
        <li data-trace="${escapeAttr(c.traceId)}" data-span="${escapeAttr(c.spanId)}">
          ${c.hasError ? '<span class="badge error">err</span>' : ""}
          ${c.anomalies.includes("out_of_range") ? '<span class="badge range">超范围</span>' : ""}
          <span class="cn">${escapeHtml(c.name)}</span>
          <span class="cd">[${c.startMs},${c.endMs}] ${fmtMs(c.durationMs)}</span>
        </li>`).join("")}</ul>`
        : '<div class="detail-empty" style="padding:4px 0">无直接子调用</div>'}
    </div>

    ${anomalyHtml}

    <div class="detail-section">
      <h3>修正时间 / 父关系</h3>
      <form class="corr-form" id="corr-form">
        <div class="row2">
          <label>startMs<input type="number" step="any" id="corr-start" value="${node.startMs}"></label>
          <label>endMs<input type="number" step="any" id="corr-end" value="${node.endMs}"></label>
        </div>
        <label>parentSpanId（留空=根；需同追踪内已存在的编号）
          <input type="text" id="corr-parent" value="${node.parentSpanId === null ? "" : escapeAttr(node.parentSpanId)}"></label>
        <label>修正原因（可选）<input type="text" id="corr-reason"></label>
        <div class="corr-actions">
          <button type="button" class="btn primary" id="corr-apply">应用修正并重新检查</button>
        </div>
        <div class="field-error hidden" id="corr-error"></div>
      </form>
    </div>

    <div class="detail-section">
      <h3>原始记录（永不修改）</h3>
      <pre class="raw-json">${escapeHtml(JSON.stringify(original, null, 2))}</pre>
    </div>

    <div class="detail-section">
      <h3>当前有效记录（${corrected ? "含修正" : "同原始"}）</h3>
      <pre class="raw-json">${escapeHtml(JSON.stringify({
        traceId: node.traceId, spanId: node.spanId, parentSpanId: node.parentSpanId,
        name: node.name, startMs: node.startMs, endMs: node.endMs,
        hasError: node.hasError,
      }, null, 2))}</pre>
    </div>`;

  root.querySelectorAll(".child-list li").forEach((li) => {
    li.addEventListener("click", () => selectNode(li.dataset.trace, li.dataset.span));
  });
  $("corr-apply").addEventListener("click", () => submitCorrection(node));
}

function escapeAttr(v) {
  return escapeHtml(v).replace(/`/g, "&#96;");
}

async function submitCorrection(node) {
  const startMs = parseFloat($("corr-start").value);
  const endMs = parseFloat($("corr-end").value);
  const parentRaw = $("corr-parent").value.trim();
  const reason = $("corr-reason").value;
  const errEl = $("corr-error");

  if (!Number.isFinite(startMs) || !Number.isFinite(endMs)) {
    showCorrError("startMs / endMs 必须是有限数字");
    return;
  }
  if (endMs < startMs) {
    showCorrError(`结束 ${endMs} 早于开始 ${startMs}，修正会被拒绝`);
    return;
  }

  const resp = await api("/api/correct", {
    method: "POST",
    body: JSON.stringify({
      traceId: node.traceId,
      spanId: node.spanId,
      startMs, endMs,
      parentSpanId: parentRaw === "" ? null : parentRaw,
      reason,
    }),
  });
  if (!resp.ok) {
    showCorrError((resp.issues || []).map((i) => `[${i.code}] ${i.message}`).join("\n"));
    showMessages(resp.issues, "error");
    return;
  }
  errEl.classList.add("hidden");
  flash("ok", "corrected", `已修正 ${node.name} 并重新构建调用树（可撤销）`);
  await refreshState(resp.state);
}

function showCorrError(msg) {
  const errEl = $("corr-error");
  errEl.textContent = msg;
  errEl.classList.remove("hidden");
}

// ---------------------------------------------------------------------------
// 导入 / 导出 / 撤销 / 清空
// ---------------------------------------------------------------------------

const importDialog = $("import-dialog");
const importText = $("import-text");

$("btn-sample").addEventListener("click", async () => {
  const data = await api("/api/sample");
  clearMessages();
  flash("ok", "sample", "已载入内置样例");
  selection = null;
  zoomViews.clear();
  await refreshState(data);
});

$("btn-import").addEventListener("click", () => {
  importText.value = "";
  if (typeof importDialog.showModal === "function") importDialog.showModal();
  else importDialog.setAttribute("open", "");
});

$("import-cancel").addEventListener("click", () => importDialog.close());

$("import-file").addEventListener("change", async (event) => {
  const file = event.target.files[0];
  if (!file) return;
  importText.value = await file.text();
});

$("import-confirm").addEventListener("click", async () => {
  let parsed;
  try {
    parsed = JSON.parse(importText.value);
  } catch (e) {
    flash("error", "bad_json", `JSON 解析失败: ${e.message}`);
    return;
  }
  const records = Array.isArray(parsed) ? parsed
    : (parsed && Array.isArray(parsed.records) ? parsed.records : null);
  if (records === null) {
    flash("error", "not_list", "导入内容必须是记录数组，或含 records 数组的对象");
    return;
  }
  const resp = await api("/api/import", {
    method: "POST",
    body: JSON.stringify({ records }),
  });
  if (!resp.ok) {
    showMessages(resp.issues, "error");
    flash("error", "rejected",
      `整批导入被拒绝（${resp.issues.length} 个问题），此前有效数据已保留`);
    return;
  }
  importDialog.close();
  clearMessages();
  selection = null;
  collapsed.clear();
  zoomViews.clear();
  flash("ok", "imported",
    `导入成功：${resp.state.spanCount} 条跨度` +
    (resp.duplicatesMerged ? `，合并 ${resp.duplicatesMerged} 条完全相同重复记录` : ""));
  await refreshState(resp.state);
});

$("btn-undo").addEventListener("click", async () => {
  const resp = await api("/api/undo", { method: "POST", body: "{}" });
  if (resp.undone) {
    flash("ok", "undo", `已撤销对 ${resp.undone.name || resp.undone.spanId} 的修正`);
  } else {
    flash("warn", "undo", "没有可撤销的修正");
  }
  await refreshState(resp.state);
});

$("btn-export").addEventListener("click", async () => {
  const bundle = await api("/api/export");
  const blob = new Blob([JSON.stringify(bundle, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "trace_studio_corrected.json";
  a.click();
  URL.revokeObjectURL(url);
  flash("ok", "export", `已导出修正副本：${bundle.records.length} 条记录，` +
    `${bundle.corrections.length} 条修正（原始输入未改动）`);
});

$("btn-clear").addEventListener("click", async () => {
  const resp = await api("/api/clear", { method: "POST", body: "{}" });
  selection = null;
  collapsed.clear();
  zoomViews.clear();
  clearMessages();
  await refreshState(resp.state);
});

// 全局缩放按钮作用于“当前选中跨度所在追踪”，否则第一个追踪
$("zoom-in").addEventListener("click", () => zoomButton(0.7));
$("zoom-out").addEventListener("click", () => zoomButton(1.42));
$("zoom-reset").addEventListener("click", () => {
  const trace = activeTrace();
  if (trace) { zoomViews.set(trace.traceId, defaultView(trace)); renderTimelines(); }
});

function activeTrace() {
  if (!state || !state.traces.length) return null;
  if (selection) {
    const t = findTrace(selection.traceId);
    if (t) return t;
  }
  return state.traces[0];
}

function zoomButton(factor) {
  const trace = activeTrace();
  if (!trace) return;
  const v = getView(trace);
  const mid = (v.winStart + v.winEnd) / 2;
  const half = ((v.winEnd - v.winStart) * factor) / 2;
  v.winStart = mid - half;
  v.winEnd = mid + half;
  renderTimelines();
  highlightTimeline();
}

// ---------------------------------------------------------------------------
// 启动
// ---------------------------------------------------------------------------

(async function init() {
  window.addEventListener("mousemove", handleDragMove);
  window.addEventListener("mouseup", handleDragUp);
  await refreshState();
})();
