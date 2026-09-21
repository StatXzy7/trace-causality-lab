"""标准库 HTTP 服务：静态前端 + JSON API。

路由：
- GET  /                 前端单页
- GET  /api/state        当前视图（有效记录分析结果）+ 原始输入与修正日志
- POST /api/load         导入 JSON（整体替换，失败保留状态）
- POST /api/correct      修正单条记录并重新检查
- POST /api/undo         撤销最近一次修正
- GET  /api/export      导出修正副本
- GET  /api/sample       内置样例内容（不落库；前端可再 POST /api/load）
- POST /api/load-sample  直接载入内置样例
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from . import samples
from .model import Studio, TraceValidationError, build_view


class StudioStore:
    """线程安全的 Studio 包装。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.studio = Studio()

    def snapshot(self) -> dict:
        with self._lock:
            view = self.studio.analyze()
            view["total_corrections"] = len(self.studio.corrections)
            view["has_import"] = bool(self.studio.original)
            view["original_input"] = {
                "records": [dict(r) for r in self.studio.original]
            }
            view["corrections"] = [dict(c) for c in self.studio.corrections]
            return view

    def load(self, payload) -> int:
        with self._lock:
            return self.studio.load(payload)

    def correct(self, body: dict) -> dict:
        with self._lock:
            kwargs: dict = {}
            if "new_span_id" in body:
                kwargs["new_span_id"] = body["new_span_id"]
            if "new_parent_span_id" in body:
                kwargs["new_parent_span_id"] = body["new_parent_span_id"]
            if "new_start_ms" in body:
                kwargs["new_start_ms"] = body["new_start_ms"]
            if "new_end_ms" in body:
                kwargs["new_end_ms"] = body["new_end_ms"]
            change = self.studio.apply_correction(
                body["trace_id"], body["span_id"], **kwargs
            )
            return change

    def undo(self) -> dict | None:
        with self._lock:
            return self.studio.undo()

    def export(self) -> dict:
        with self._lock:
            return self.studio.export()

    def load_sample(self) -> int:
        with self._lock:
            return self.studio.load(samples.sample_payload())


def make_handler(store: StudioStore):
    class Handler(BaseHTTPRequestHandler):
        server_version = "TraceStudio/1.0"

        # -- 基础工具 -----------------------------------------------------

        def _send_json(self, obj, status: int = 200) -> None:
            data = json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _send_html(self, text: str, status: int = 200) -> None:
            data = text.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _read_json_body(self):
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0:
                return {}
            raw = self.rfile.read(length)
            try:
                return json.loads(raw.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise TraceValidationError(f"请求体不是合法 JSON：{exc}") from exc

        def log_message(self, fmt, *args):  # 安静一点
            pass

        # -- 路由 ---------------------------------------------------------

        def do_GET(self):  # noqa: N802 (标准库命名)
            path = urlparse(self.path).path
            try:
                if path == "/":
                    from .frontend import INDEX_HTML

                    self._send_html(INDEX_HTML)
                elif path == "/api/state":
                    self._send_json(store.snapshot())
                elif path == "/api/export":
                    self._send_json(store.export())
                elif path == "/api/sample":
                    self._send_json(samples.sample_payload())
                else:
                    self._send_json({"error": "not found"}, status=404)
            except Exception as exc:  # 服务端兜底，不吞掉细节
                self._send_json({"error": f"服务器错误：{exc}"}, status=500)

        def do_POST(self):  # noqa: N802
            path = urlparse(self.path).path
            try:
                if path == "/api/load":
                    body = self._read_json_body()
                    count = store.load(body)
                    self._send_json(
                        {"ok": True, "imported": count, "state": store.snapshot()}
                    )
                elif path == "/api/load-sample":
                    count = store.load_sample()
                    self._send_json(
                        {"ok": True, "imported": count, "state": store.snapshot()}
                    )
                elif path == "/api/correct":
                    body = self._read_json_body()
                    if "trace_id" not in body or "span_id" not in body:
                        raise TraceValidationError("修正请求必须包含 trace_id 与 span_id")
                    change = store.correct(body)
                    self._send_json(
                        {"ok": True, "change": change, "state": store.snapshot()}
                    )
                elif path == "/api/undo":
                    change = store.undo()
                    self._send_json(
                        {"ok": True, "undone": change, "state": store.snapshot()}
                    )
                else:
                    self._send_json({"error": "not found"}, status=404)
            except TraceValidationError as exc:
                # 422：校验失败，语义上“已保留此前有效工作”
                self._send_json({"ok": False, "error": str(exc)}, status=422)
            except Exception as exc:
                self._send_json({"ok": False, "error": f"服务器错误：{exc}"}, status=500)

    return Handler


def create_server(port: int = 0, host: str = "127.0.0.1") -> ThreadingHTTPServer:
    store = StudioStore()
    server = ThreadingHTTPServer((host, port), make_handler(store))
    server.store = store  # type: ignore[attr-defined]
    return server


def serve(port: int = 0, host: str = "127.0.0.1") -> int:
    server = create_server(port, host)
    actual_host, actual_port = server.server_address[:2]
    print(f"Trace Studio 已启动：http://{actual_host}:{actual_port}")
    print("按 Ctrl+C 停止。")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n正在停止…")
    finally:
        server.server_close()
    return actual_port
