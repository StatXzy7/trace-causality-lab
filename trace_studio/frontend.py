"""前端单页：作为常量内嵌，零构建、零第三方依赖。

深色“诊断仪器”风格：时间轴泳道 + 调用树 + 原始记录/耗时面板 + 修正工作台。
"""

from __future__ import annotations

INDEX_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Trace Studio · 本地追踪诊断</title>
<style>
  :root{
    --ink:#0d1017; --panel:#141925; --panel2:#1a2130; --line:#283142;
    --text:#e6ebf4; --muted:#8b97ab; --faint:#5b6678;
    --accent:#e8b14a; --accent2:#7aa2ff; --teal:#37d2be;
    --err:#ff5d73; --orphan:#f0a14f; --ok:#4ade80;
    --mono:"Cascadia Code","SFMono-Regular",Consolas,"Courier New",monospace;
  }
  *{box-sizing:border-box}
  html,body{margin:0;height:100%}
  body{background:var(--ink);color:var(--text);font:13px/1.55 var(--mono);
       background-image:radial-gradient(1200px 500px at 80% -10%,#18203300 60%,#1a243822 100%)}
  button{font:inherit;color:var(--text);background:var(--panel2);border:1px solid var(--line);
         border-radius:5px;padding:5px 12px;cursor:pointer}
  button:hover{border-color:var(--accent2);color:#fff}
  button.primary{border-color:#6d86b8;background:#1d2740}
  button.danger{border-color:#7a3545;color:#ffb3be}
  button:disabled{opacity:.4;cursor:not-allowed}
  input,textarea,select{font:inherit;background:#0c1018;border:1px solid var(--line);
         color:var(--text);border-radius:5px;padding:5px 8px}
  input:focus,textarea:focus{outline:none;border-color:var(--accent2)}
  header{display:flex;align-items:baseline;gap:14px;padding:10px 16px;border-bottom:1px solid var(--line);
         background:linear-gradient(180deg,#121724,#0e131d)}
  header h1{font-size:15px;margin:0;letter-spacing:2px}
  header h1 b{color:var(--accent)}
  header .sub{color:var(--faint);font-size:11px;letter-spacing:1px}
  header .grow{flex:1}
  #corrBadge{color:var(--accent);font-size:11px;border:1px solid #5c4818;border-radius:10px;padding:1px 10px}
  .layout{display:grid;grid-template-columns:300px 1fr 340px;gap:1px;height:calc(100vh - 47px);
          background:var(--line)}
  .pane{background:var(--panel);overflow:auto;min-height:0}
  .pane h2,.pane h3{font-size:11px;letter-spacing:2px;color:var(--muted);margin:0;padding:10px 14px 6px;
         text-transform:uppercase;border-bottom:1px solid #1e2636}
  .section{border-bottom:1px solid #1e2636}
  /* 左：导入 + 树 */
  #importBox{padding:10px 14px;display:flex;flex-direction:column;gap:8px}
  #jsonInput{width:100%;height:130px;resize:vertical}
  .row{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
  #banner{font-size:12px;border-radius:5px;padding:7px 10px;display:none;white-space:pre-wrap}
  #banner.err{display:block;background:#2a141b;border:1px solid #5e2a36;color:#ff9fab}
  #banner.ok{display:block;background:#12241a;border:1px solid #25543a;color:#8ee6ac}
  ul.tree{list-style:none;margin:0;padding:4px 0}
  ul.tree ul{margin:0 0 0 12px;padding-left:8px;border-left:1px dotted #2b3548}
  li.node{padding:2px 6px;border-radius:4px;cursor:pointer;white-space:nowrap;text-overflow:ellipsis;
          overflow:hidden}
  li.node:hover{background:#1e283c}
  li.node.sel{background:#243350;box-shadow:inset 2px 0 0 var(--accent2)}
  li.node .nm{color:var(--text)}
  li.node .meta{color:var(--faint);font-size:11px;margin-left:6px}
  .tag{font-size:10px;border-radius:3px;padding:0 5px;margin-left:4px;border:1px solid}
  .tag.err{color:#ff9fab;border-color:#7a3545;background:#241016}
  .tag.orph{color:#f6c17a;border-color:#6b4d1c;background:#241a0c}
  .tag.warn{color:#9ec7ff;border-color:#34507a;background:#101c2e}
  .orphHead{color:var(--orphan);padding:8px 14px 2px;font-size:11px;letter-spacing:1px}
  /* 中：时间轴 */
  #center{display:flex;flex-direction:column;background:var(--panel);min-width:0}
  #tlToolbar{display:flex;gap:8px;align-items:center;padding:8px 14px;border-bottom:1px solid #1e2636}
  #traceTabs{display:flex;gap:6px;flex-wrap:wrap}
  .ttab{border:1px solid var(--line);border-radius:5px;padding:3px 10px;cursor:pointer;background:var(--panel2)}
  .ttab.on{border-color:var(--accent);color:var(--accent)}
  #tlScroll{flex:1;overflow:auto;position:relative;min-height:0;cursor:grab}
  #tlScroll.drag{cursor:grabbing}
  #tlCanvas{position:relative;min-width:100%}
  .axis{position:sticky;top:0;height:26px;background:#10151f;border-bottom:1px solid var(--line);z-index:5}
  .tick{position:absolute;top:0;height:26px;border-left:1px solid #2a3446;color:var(--faint);
        font-size:10px;padding-left:4px}
  .lane{position:absolute;left:0;height:30px;border-bottom:1px solid #171e2c}
  .lane:hover{background:#172032}
  .bar{position:absolute;height:18px;top:6px;border-radius:3px;background:linear-gradient(180deg,#6f97f0,#4a6fc4);
       border:1px solid #8fb0ff55;min-width:2px;cursor:pointer;overflow:hidden}
  .bar .lab{position:absolute;left:6px;top:1px;font-size:11px;color:#eef3ff;white-space:nowrap;
       text-shadow:0 1px 2px #000000aa}
  .bar.sel{outline:2px solid #fff;outline-offset:0}
  .bar.err{background:linear-gradient(180deg,#ff7085,#c8374f);border-color:#ffb0bc66}
  .bar.orphan{background:repeating-linear-gradient(45deg,#5a3f16,#5a3f16 5px,#6e4d1b 5px,#6e4d1b 10px);
       border-color:#f0a14f88}
  .bar.oor{box-shadow:inset 0 0 0 1px #ff5d73}
  .zero{position:absolute;top:8px;width:14px;height:14px;transform:rotate(45deg);
       background:var(--teal);border:1px solid #a7f3e8;cursor:pointer;border-radius:2px}
  .zero.orphan{background:var(--orphan)}
  .zero.err{background:var(--err)}
  .gridline{position:absolute;top:0;bottom:0;border-left:1px solid #1c2433}
  #emptyState{flex:1;display:flex;align-items:center;justify-content:center;flex-direction:column;gap:10px;
       color:var(--muted)}
  #emptyState b{color:var(--accent);font-size:15px;letter-spacing:2px}
  /* 右：详情 + 修正 */
  #detail{padding:12px 14px;display:flex;flex-direction:column;gap:10px}
  .kv{display:grid;grid-template-columns:96px 1fr;gap:3px 10px;font-size:12px}
  .kv .k{color:var(--faint)}
  .bigTime{font-size:22px;color:var(--teal)}
  .bigTime small{font-size:12px;color:var(--muted)}
  pre.raw{background:#0b0f16;border:1px solid var(--line);border-radius:6px;padding:8px;font-size:11px;
       max-height:220px;overflow:auto;margin:0;white-space:pre-wrap;word-break:break-all}
  .anom li{color:#f6c17a}
  form.corr{display:grid;grid-template-columns:1fr 1fr;gap:8px}
  form.corr label{display:flex;flex-direction:column;gap:3px;font-size:11px;color:var(--faint)}
  form.corr .full{grid-column:1 / -1}
  .hint{color:var(--faint);font-size:11px}
  .childChip{display:inline-block;border:1px solid var(--line);border-radius:4px;padding:1px 7px;margin:2px 4px 2px 0;
       cursor:pointer;background:var(--panel2);font-size:11px}
  .childChip:hover{border-color:var(--accent2)}
  ::-webkit-scrollbar{width:10px;height:10px}
  ::-webkit-scrollbar-thumb{background:#2a3446;border-radius:6px}
</style>
</head>
<body>
<header>
  <h1><b>TRACE</b> STUDIO</h1>
  <span class="sub">本地追踪诊断 · 父子时序 / 自身耗时 / 错误归属</span>
  <span class="grow"></span>
  <span id="corrBadge" style="display:none"></span>
  <button id="undoBtn" class="danger" disabled>↩ 撤销修正</button>
  <button id="exportBtn" class="primary" disabled>⬇ 导出修正副本</button>
</header>
<div class="layout">
  <!-- 左栏：导入 + 调用树 -->
  <div class="pane">
    <div class="section">
      <h2>导入 JSON</h2>
      <div id="importBox">
        <textarea id="jsonInput" placeholder='[{"trace_id":"t1","span_id":"s1","parent_span_id":null,"name":"root","start_ms":0,"end_ms":10}]'></textarea>
        <div class="row">
          <button id="loadBtn" class="primary">导入并检查</button>
          <button id="sampleBtn">载入内置样例</button>
          <button id="clearBtn">置空</button>
          <input type="file" id="fileInput" accept=".json,application/json">
        </div>
        <div id="banner"></div>
        <div class="hint">需要字段：trace_id / span_id / parent_span_id / name / start_ms / end_ms。
          完全相同的重复记录会合并；同 id 冲突、循环父关系、end&lt;start、非有限时间将被整体拒绝并保留当前数据。</div>
      </div>
    </div>
    <div class="section">
      <h2>调用树</h2>
      <div id="treeHost"><div class="hint" style="padding:10px 14px">尚未导入数据。</div></div>
    </div>
  </div>

  <!-- 中栏：时间轴 -->
  <div id="center">
    <div id="tlToolbar">
      <div id="traceTabs"></div>
      <span style="flex:1"></span>
      <button id="zoomOut">−</button>
      <span id="zoomLabel" class="hint"></span>
      <button id="zoomIn">＋</button>
      <button id="zoomReset">适应宽度</button>
    </div>
    <div id="tlScroll"><div id="tlCanvas"></div></div>
    <div id="emptyState" style="display:none">
      <b>空</b>
      <span id="emptyMsg">尚未导入数据 —— 粘贴 JSON、选择文件，或载入内置样例。</span>
    </div>
  </div>

  <!-- 右栏：详情 + 修正 -->
  <div class="pane">
    <h2>跨度详情</h2>
    <div id="detail">
      <div class="hint">点击时间轴或调用树中的跨度，查看原始记录、直接子调用与自身耗时。</div>
    </div>
  </div>
</div>

<script>
"use strict";
/* ============================== 状态 ============================== */
let STATE = null;            // /api/state 快照
let activeTraceId = null;
let selectedKey = null;
let scale = 1;               // 每毫秒像素数
const LANE_H = 30, AXIS_H = 26;
const $ = (id) => document.getElementById(id);

function banner(msg, ok){
  const b = $("banner");
  b.textContent = msg; b.className = ok ? "ok" : "err";
}
function clearBanner(){ $("banner").className = ""; }

async function api(path, opts){
  const res = await fetch(path, opts);
  const data = await res.json().catch(() => ({}));
  if(!res.ok || data.ok === false){
    throw new Error(data.error || ("HTTP " + res.status));
  }
  return data;
}
function post(path, body){
  return api(path, {method:"POST", headers:{"Content-Type":"application/json"},
                    body: JSON.stringify(body)});
}

/* ============================== 数据接收 ============================== */
function adoptState(state, preferKey){
  STATE = state;
  const ids = state.traces.map(t => t.trace_id);
  if(!ids.includes(activeTraceId)) activeTraceId = ids[0] || null;
  if(preferKey) selectedKey = preferKey;
  if(selectedKey && !findNode(selectedKey)) selectedKey = null;
  renderAll();
}

function flattenTrace(trace){
  const out = [];
  const walk = (n) => { out.push(n); n.children.forEach(walk); };
  trace.roots.forEach(walk); trace.orphans.forEach(walk);
  return out;
}
function activeTrace(){
  return STATE && STATE.traces.find(t => t.trace_id === activeTraceId) || null;
}
function findNode(key, trace){
  trace = trace || activeTrace();
  if(!trace) return null;
  const all = flattenTrace(trace);
  return all.find(n => n.key === key) || null;
}
function findNodeAnyTrace(key){
  if(!STATE) return null;
  for(const t of STATE.traces){
    const n = findNode(key, t);
    if(n) return {trace:t, node:n};
  }
  return null;
}

/* ============================== 调用树 ============================== */
function renderTree(){
  const host = $("treeHost");
  if(!STATE || STATE.empty){
    host.innerHTML = STATE
      ? '<div class="hint" style="padding:10px 14px">数据集为空（0 条记录）。</div>'
      : '<div class="hint" style="padding:10px 14px">尚未导入数据。</div>';
    return;
  }
  const parts = [];
  for(const t of STATE.traces){
    const head = t.trace_id === activeTraceId ? "▸ " : "  ";
    parts.push(`<div class="orphHead" style="color:var(--muted)">${head}trace: ${esc(t.trace_id)} `
      + `<span class="meta">(${t.span_count} spans, ${fmt(t.duration_ms)}ms)</span></div>`);
    parts.push(renderTreeNodes(t.roots));
    if(t.orphans.length){
      parts.push(`<div class="orphHead">⚠ 孤立跨度（缺失父记录 ${t.orphans.length}）——未虚构父节点</div>`);
      parts.push(renderTreeNodes(t.orphans));
    }
  }
  host.innerHTML = parts.join("");
  host.querySelectorAll("li.node").forEach(li => {
    li.addEventListener("click", () => {
      const hit = findNodeAnyTrace(li.dataset.key);
      if(hit){ activeTraceId = hit.trace.trace_id; selectedKey = hit.node.key; renderAll(); }
    });
  });
}
function renderTreeNodes(nodes){
  if(!nodes.length) return "";
  return "<ul class='tree'>" + nodes.map(n => {
    const tags = [];
    if(n.is_error) tags.push("<span class='tag err'>ERROR</span>");
    if(n.is_orphan || n.anomalies.some(a=>a.startsWith("orphan")))
      tags.push("<span class='tag orph'>孤立</span>");
    if(n.anomalies.some(a => a.includes("out_of_range")))
      tags.push("<span class='tag warn'>越界</span>");
    if(n.duration_ms === 0) tags.push("<span class='tag warn'>0ms</span>");
    return `<li class="node ${n.key===selectedKey?"sel":""}" data-key="${esc(n.key)}">`
      + `<span class="nm">${esc(n.name)}</span>`
      + `<span class="meta">${esc(n.span_id)} · ${fmt(n.duration_ms)}ms · 自身 ${fmt(n.self_time_ms)}ms</span>`
      + tags.join("") + renderTreeNodes(n.children) + "</li>";
  }).join("") + "</ul>";
}

/* ============================== trace 标签 ============================== */
function renderTabs(){
  const host = $("traceTabs");
  if(!STATE || STATE.empty){ host.innerHTML = ""; return; }
  host.innerHTML = STATE.traces.map(t =>
    `<span class="ttab ${t.trace_id===activeTraceId?"on":""}" data-tid="${esc(t.trace_id)}">`
    + `${esc(t.trace_id)} · ${t.span_count} spans`
    + (t.error_count ? ` · <span style="color:var(--err)">${t.error_count} 错误</span>` : "")
    + (t.anomaly_count ? ` · <span style="color:var(--orphan)">${t.anomaly_count} 异常</span>` : "")
    + "</span>").join("");
  host.querySelectorAll(".ttab").forEach(el =>
    el.addEventListener("click", () => { activeTraceId = el.dataset.tid; selectedKey = null; renderAll(); }));
}

/* ============================== 时间轴 ============================== */
function defaultScale(trace){
  const width = Math.max(1, $("tlScroll").clientWidth - 20);
  return Math.min(20, width / Math.max(1, trace.duration_ms));
}
function niceStep(pxPerMs){
  const targetPx = 110;
  const raw = targetPx / pxPerMs;
  const cands = [0.01,0.02,0.05,0.1,0.2,0.5,1,2,5,10,20,50,100,200,500,1000,2000,5000,10000,30000,60000];
  return cands.find(c => c >= raw) || cands[cands.length-1];
}
function renderTimeline(){
  const trace = activeTrace();
  const scroll = $("tlScroll"), canvas = $("tlCanvas");
  const empty = $("emptyState");
  if(!STATE || STATE.empty || !trace){
    scroll.style.display = "none"; empty.style.display = "flex";
    $("emptyMsg").textContent = !STATE
      ? "尚未导入数据 —— 粘贴 JSON、选择文件，或载入内置样例。"
      : "数据集为空（0 条记录）：已明确按空数据处理。";
    $("zoomLabel").textContent = "";
    return;
  }
  scroll.style.display = "block"; empty.style.display = "none";

  const nodes = flattenTrace(trace);
  const maxLane = Math.max(0, ...nodes.map(n => n.lane || 0));
  const t0 = trace.start_ms, dur = Math.max(1, trace.duration_ms);
  if(!scale || !Number.isFinite(scale)) scale = defaultScale(trace);
  const width = Math.max(scroll.clientWidth - 4, dur * scale + 80);
  const height = AXIS_H + (maxLane + 1) * LANE_H;
  canvas.style.width = width + "px";
  canvas.style.height = height + "px";

  const step = niceStep(scale);
  let html = `<div class="axis" style="width:${width}px">`;
  for(let t = Math.ceil(t0/step)*step; t <= trace.end_ms + 1e-9; t += step){
    const x = (t - t0) * scale;
    html += `<div class="tick" style="left:${x}px">${fmt(t)}ms</div>`;
    html += `<div class="gridline" style="left:${x}px;height:${height-AXIS_H}px;top:${AXIS_H}px"></div>`;
  }
  html += "</div>";

  for(let lane = 0; lane <= maxLane; lane++){
    html += `<div class="lane" style="top:${AXIS_H + lane*LANE_H}px;width:${width}px"></div>`;
  }
  for(const n of nodes){
    const x = (n.start_ms - t0) * scale;
    const top = AXIS_H + (n.lane || 0) * LANE_H;
    const isOrph = n.is_orphan || n.anomalies.some(a => a.startsWith("orphan"));
    const oor = n.anomalies.some(a => a.includes("out_of_parent"));
    const cls = ["bar", n.is_error ? "err" : "", isOrph ? "orphan" : "",
                 oor ? "oor" : "", n.key === selectedKey ? "sel" : ""].join(" ");
    if(n.duration_ms === 0){
      const zcls = ["zero", n.is_error?"err":"", isOrph?"orphan":"",
                    n.key===selectedKey?"sel":""].join(" ");
      html += `<div class="${zcls}" data-key="${esc(n.key)}" style="left:${x-7}px;top:${top+8}px"
                title="${barTitle(n)}"></div>`;
    } else {
      const w = Math.max(2, n.duration_ms * scale);
      html += `<div class="${cls}" data-key="${esc(n.key)}"
                style="left:${x}px;width:${w}px;top:${top+6}px" title="${barTitle(n)}">`
           + (w > 70 ? `<span class="lab">${esc(n.name)}</span>` : "") + "</div>";
    }
  }
  canvas.innerHTML = html;
  canvas.querySelectorAll("[data-key]").forEach(el =>
    el.addEventListener("click", (e) => { e.stopPropagation(); selectedKey = el.dataset.key; renderAll(); }));
  $("zoomLabel").textContent = (scale >= 1 ? scale.toFixed(1) : scale.toFixed(2)) + " px/ms";
}
function barTitle(n){
  return esc(`${n.name}\n${n.span_id}  ${fmt(n.start_ms)}→${fmt(n.end_ms)}ms`
    + `\n总 ${fmt(n.duration_ms)}ms · 自身 ${fmt(n.self_time_ms)}ms`
    + (n.is_error ? "\nERROR" : ""));
}

/* 缩放（以鼠标位置为锚）与拖拽平移 */
function zoomAt(factor, clientX){
  const trace = activeTrace(); if(!trace) return;
  const scroll = $("tlScroll");
  const rect = scroll.getBoundingClientRect();
  const anchor = clientX == null ? scroll.clientWidth/2 : clientX - rect.left + scroll.scrollLeft;
  const tAt = trace.start_ms + anchor / scale;
  const next = Math.min(500, Math.max(0.002, scale * factor));
  scale = next;
  renderTimeline();
  scroll.scrollLeft = (tAt - trace.start_ms) * scale - (clientX == null ? scroll.clientWidth/2 : clientX - rect.left);
}
$("zoomIn").onclick = () => zoomAt(1.4);
$("zoomOut").onclick = () => zoomAt(1/1.4);
$("zoomReset").onclick = () => { const t = activeTrace(); if(t){ scale = defaultScale(t); renderTimeline(); } };
$("tlScroll").addEventListener("wheel", (e) => {
  if(!activeTrace()) return;
  e.preventDefault();
  zoomAt(e.deltaY < 0 ? 1.15 : 1/1.15, e.clientX);
}, {passive:false});
(function setupDrag(){
  const s = $("tlScroll"); let down = false, sx = 0, sl = 0, moved = false;
  s.addEventListener("mousedown", (e) => {
    if(e.target.dataset && e.target.dataset.key) return; // 点在条上不拖动
    down = true; moved = false; sx = e.clientX; sl = s.scrollLeft; s.classList.add("drag");
  });
  window.addEventListener("mousemove", (e) => {
    if(!down) return; moved = true; s.scrollLeft = sl - (e.clientX - sx);
  });
  window.addEventListener("mouseup", () => { down = false; s.classList.remove("drag"); });
  s.addEventListener("click", (e) => { if(moved) e.stopPropagation(); }, true);
})();

/* ============================== 详情面板 ============================== */
function renderDetail(){
  const host = $("detail");
  const n = selectedKey ? findNode(selectedKey) : null;
  if(!n){
    host.innerHTML = '<div class="hint">点击时间轴或调用树中的跨度，查看原始记录、直接子调用与自身耗时。</div>';
    return;
  }
  const selfShare = n.duration_ms > 0 ? (n.self_time_ms / n.duration_ms * 100) : 0;
  const anoms = n.anomalies.length
    ? `<ul class="anom" style="margin:4px 0;padding-left:18px">${n.anomalies.map(a=>`<li>${esc(a)}</li>`).join("")}</ul>`
    : '<span class="hint">无</span>';
  const errBlock = n.is_error
    ? `<div style="border:1px solid #7a3545;background:#241016;color:#ff9fab;border-radius:6px;padding:7px 9px">
         ⛝ ERROR：${esc(n.raw.error != null ? String(n.raw.error) : "该跨度被标记为错误（is_error=true）")}</div>` : "";
  const children = n.children.length
    ? n.children.map(c =>
        `<span class="childChip" data-key="${esc(c.key)}">${esc(c.name)} · ${fmt(c.duration_ms)}ms</span>`).join("")
    : '<span class="hint">无直接子调用</span>';
  host.innerHTML = `
    <div><b style="font-size:14px">${esc(n.name)}</b>
      ${n.is_error?'<span class="tag err">ERROR</span>':''}
      ${n.anomalies.some(a=>a.startsWith("orphan"))?'<span class="tag orph">孤立</span>':''}</div>
    ${errBlock}
    <div class="kv">
      <span class="k">trace_id</span><span>${esc(n.trace_id)}</span>
      <span class="k">span_id</span><span>${esc(n.span_id)}</span>
      <span class="k">parent</span><span>${n.parent_span_id == null ? '<i>根（无父）</i>' : esc(n.parent_span_id)}</span>
      <span class="k">时间区间</span><span>${fmt(n.start_ms)} → ${fmt(n.end_ms)} ms</span>
      <span class="k">总时长</span><span>${fmt(n.duration_ms)} ms</span>
    </div>
    <div class="bigTime">${fmt(n.self_time_ms)} <small>ms 自身耗时</small>
      <div class="hint">= 总时长 ${fmt(n.duration_ms)} − 直接子跨度在父范围内的合并覆盖 ${fmt(n.child_coverage_ms)}
      （并行重叠只扣一次）；自身占比 ${n.duration_ms>0 ? selfShare.toFixed(1)+"%" : "—"}</div></div>
    <div><div class="k hint">异常</div>${anoms}</div>
    <div><div class="k hint">直接子调用（${n.child_count}）</div><div>${children}</div></div>
    <div><div class="k hint">原始记录（只读，修正不会改动它）</div><pre class="raw">${esc(JSON.stringify(n.raw, null, 2))}</pre></div>
    ${correctionForm(n)}`;
  host.querySelectorAll(".childChip").forEach(el =>
    el.addEventListener("click", () => { selectedKey = el.dataset.key; renderAll(); }));
  host.querySelector("form.corr").addEventListener("submit", onSubmitCorrection);
}

function correctionForm(n){
  return `<div class="section" style="padding-top:8px;border-top:1px solid #1e2636">
    <h3 style="padding:0 0 6px">修正这条记录（重新检查后重建树）</h3>
    <form class="corr" onsubmit="return false">
      <label>新 span_id<input name="new_span_id" value="${escAttr(n.span_id)}"></label>
      <label>新 parent_span_id（留空=根）<input name="new_parent_span_id" value="${n.parent_span_id==null?"":escAttr(n.parent_span_id)}"></label>
      <label>新 start_ms<input name="new_start_ms" value="${n.start_ms}"></label>
      <label>新 end_ms<input name="new_end_ms" value="${n.end_ms}"></label>
      <div class="full row"><button class="primary" type="submit">应用修正并重新检查</button>
        <span class="hint">非法修正（循环、end&lt;start、冲突、跨 trace 串联）会被拒绝，当前树保持不变。</span></div>
    </form></div>`;
}

async function onSubmitCorrection(e){
  e.preventDefault();
  const n = findNode(selectedKey); if(!n) return;
  const f = e.target;
  const body = {trace_id: n.trace_id, span_id: n.span_id};
  const vSid = f.new_span_id.value.trim();
  if(vSid && vSid !== n.span_id) body.new_span_id = vSid;
  const vPar = f.new_parent_span_id.value.trim();
  if(vPar !== (n.parent_span_id == null ? "" : n.parent_span_id))
    body.new_parent_span_id = vPar === "" ? null : vPar;
  const vS = f.new_start_ms.value.trim(), vE = f.new_end_ms.value.trim();
  if(vS !== String(n.start_ms)){
    const x = Number(vS);
    if(!Number.isFinite(x)){ banner("新 start_ms 必须是有限数字", false); return; }
    body.new_start_ms = x;
  }
  if(vE !== String(n.end_ms)){
    const x = Number(vE);
    if(!Number.isFinite(x)){ banner("新 end_ms 必须是有限数字", false); return; }
    body.new_end_ms = x;
  }
  if(Object.keys(body).length === 2){ banner("没有检测到字段变更", false); return; }
  try{
    const data = await post("/api/correct", body);
    banner("修正已应用，已重新检查并重建调用树。", true);
    const nk = body.new_span_id || n.span_id;
    adoptState(data.state, `${n.trace_id}::${nk}`);
  }catch(err){ banner(err.message, false); }
}

/* ============================== 顶部按钮 ============================== */
$("undoBtn").onclick = async () => {
  try{
    const data = await post("/api/undo", {});
    if(!data.undone){ banner("没有可撤销的修正。", false); return; }
    banner("已撤销最近一次修正。", true);
    adoptState(data.state, null);
  }catch(err){ banner(err.message, false); }
};
$("exportBtn").onclick = async () => {
  const data = await api("/api/export");
  const blob = new Blob([JSON.stringify(data, null, 2)], {type:"application/json"});
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "trace-studio-corrected.json";
  a.click();
  URL.revokeObjectURL(a.href);
};

/* ============================== 导入 ============================== */
async function doLoad(payload){
  try{
    const data = await post("/api/load", payload);
    clearBanner();
    banner(`导入成功：${data.imported} 条记录（完全相同的重复已合并）。`, true);
    selectedKey = null; scale = 0;
    adoptState(data.state, null);
  }catch(err){ banner("导入被拒绝，已保留当前数据：\n" + err.message, false); }
}
$("loadBtn").onclick = () => {
  const txt = $("jsonInput").value.trim();
  if(txt === ""){ doLoad([]); return; }   // 空输入 => 明确的空数据集
  let parsed;
  try{ parsed = JSON.parse(txt); }
  catch(e){ banner("JSON 解析失败（未发送到服务器，当前数据不变）：\n" + e.message, false); return; }
  doLoad(parsed);
};
$("sampleBtn").onclick = async () => {
  const data = await post("/api/load-sample", {});
  $("jsonInput").value = JSON.stringify(data.state.original_input, null, 2);
  banner("已载入内置样例：并行重叠子调用、缺失父记录、错误与零时长、跨 trace 同 id。", true);
  selectedKey = null; scale = 0;
  adoptState(data.state, null);
};
$("clearBtn").onclick = () => { $("jsonInput").value = ""; doLoad([]); };
$("fileInput").addEventListener("change", () => {
  const file = $("fileInput").files[0];
  if(!file) return;
  const reader = new FileReader();
  reader.onload = () => {
    $("jsonInput").value = String(reader.result || "");
    $("loadBtn").click();
  };
  reader.readAsText(file, "utf-8");
});

/* ============================== 渲染调度 ============================== */
function renderChrome(){
  const n = STATE && STATE.total_corrections ? STATE.total_corrections : 0;
  $("corrBadge").style.display = n ? "" : "none";
  $("corrBadge").textContent = n ? `已应用 ${n} 条修正（可撤销）` : "";
  $("undoBtn").disabled = !n;
  $("exportBtn").disabled = !(STATE && STATE.has_import);
}
function renderAll(){
  renderChrome(); renderTabs(); renderTree(); renderTimeline(); renderDetail();
}

/* ============================== 工具 ============================== */
function esc(s){ return String(s).replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c])); }
function escAttr(s){ return esc(s); }
function fmt(x){
  if(x == null) return "";
  const n = Number(x);
  return Math.abs(n) >= 100 ? n.toFixed(0) : (Math.round(n*1000)/1000).toString();
}

/* 启动 */
api("/api/state").then(adoptState).catch(err => banner("初始化失败：" + err.message, false));
</script>
</body>
</html>
"""
