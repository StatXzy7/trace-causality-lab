"""标准库 HTTP 服务：静态页面 + JSON API。

仅依赖 ``http.server``，无第三方包。状态保存在单进程内存中。
"""

from __future__ import annotations

import json
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .corrections import Correction
from .parser import parse_records
from .sample import sample_records
from .store import TraceStore

_WEB_DIR = Path(__file__).resolve().parent / "web"

_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
}


class TraceRequestHandler(BaseHTTPRequestHandler):
    store: TraceStore | None = None  # 由 build_server 注入的子类属性

    # 本地诊断工具无需逐条访问日志
    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
        return

    # ------------------------------------------------------------------
    # 基础工具
    # ------------------------------------------------------------------
    def _send_json(self, payload: Any, status: int = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> Any:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        return json.loads(raw.decode("utf-8"))

    def _send_static(self, rel_path: str) -> None:
        # 防路径穿越：只允许 web 目录内的普通文件
        target = (_WEB_DIR / rel_path).resolve()
        try:
            target.relative_to(_WEB_DIR.resolve())
        except ValueError:
            self.send_error(HTTPStatus.FORBIDDEN)
            return
        if not target.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        body = target.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header(
            "Content-Type",
            _CONTENT_TYPES.get(target.suffix, "application/octet-stream"),
        )
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    # ------------------------------------------------------------------
    # 路由
    # ------------------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            self._send_static("index.html")
        elif path == "/api/state":
            self._send_json(self.store.state_dict())
        elif path == "/api/sample":
            result = parse_records(sample_records())
            self.store.load_parse_result(result)
            self._send_json(self.store.state_dict())
        elif path == "/api/export":
            self._send_json(self.store.export_bundle())
        elif path.startswith("/web/"):
            self._send_static(path[len("/web/") :])
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        try:
            payload = self._read_json()
        except (ValueError, UnicodeDecodeError) as exc:
            self._send_json(
                {
                    "ok": False,
                    "state": self.store.state_dict(),
                    "issues": [
                        {"code": "bad_json", "message": f"请求体不是合法 JSON: {exc}"}
                    ],
                },
                status=HTTPStatus.BAD_REQUEST,
            )
            return

        if path == "/api/import":
            records = payload.get("records") if isinstance(payload, dict) else payload
            result = parse_records(records)
            action = self.store.load_parse_result(result)
            response = {
                "ok": action.ok,
                "issues": [issue.to_dict() for issue in action.issues],
                "duplicatesMerged": action.duplicates_merged,
                "state": self.store.state_dict(),
            }
            self._send_json(
                response,
                status=HTTPStatus.OK if action.ok else HTTPStatus.BAD_REQUEST,
            )
            return

        if path == "/api/correct":
            correction = self._correction_from_payload(payload)
            if isinstance(correction, str):
                self._send_json(
                    {"ok": False, "issues": [{"code": "bad_type", "message": correction}],
                     "state": self.store.state_dict()},
                    status=HTTPStatus.BAD_REQUEST,
                )
                return
            action = self.store.apply_correction(correction)
            self._send_json(
                {
                    "ok": action.ok,
                    "issues": [issue.to_dict() for issue in action.issues],
                    "state": self.store.state_dict(),
                },
                status=HTTPStatus.OK if action.ok else HTTPStatus.BAD_REQUEST,
            )
            return

        if path == "/api/undo":
            popped = self.store.undo()
            self._send_json(
                {
                    "ok": True,
                    "undone": popped.to_dict() if popped is not None else None,
                    "state": self.store.state_dict(),
                }
            )
            return

        if path == "/api/clear":
            self.store.clear()
            self._send_json({"ok": True, "state": self.store.state_dict()})
            return

        self.send_error(HTTPStatus.NOT_FOUND)

    @staticmethod
    def _correction_from_payload(payload: Any) -> Correction | str:
        if not isinstance(payload, dict):
            return "修正请求必须是 JSON 对象"
        try:
            trace_id = payload["traceId"]
            span_id = payload["spanId"]
            start_ms = float(payload["startMs"])
            end_ms = float(payload["endMs"])
        except (KeyError, TypeError, ValueError):
            return "缺少 traceId/spanId/startMs/endMs 或时间不是数字"
        parent = payload.get("parentSpanId", None)
        if parent is not None and not isinstance(parent, str):
            return "parentSpanId 必须是字符串或 null"
        reason = payload.get("reason", "")
        if not isinstance(reason, str):
            reason = ""
        return Correction(
            trace_id=trace_id,
            span_id=span_id,
            start_ms=start_ms,
            end_ms=end_ms,
            parent_id=parent if isinstance(parent, str) and parent.strip() else None,
            reason=reason,
        )


def build_server(host: str = "127.0.0.1", port: int = 0) -> ThreadingHTTPServer:
    """创建并绑定服务器（不开始 serve）。port=0 时由系统分配端口。"""
    store = TraceStore()

    class BoundHandler(TraceRequestHandler):
        pass

    BoundHandler.store = store
    server = ThreadingHTTPServer((host, port), BoundHandler)
    server.trace_store = store  # type: ignore[attr-defined]
    return server


def serve(host: str = "127.0.0.1", port: int = 0, verbose: bool = True) -> ThreadingHTTPServer:
    server = build_server(host, port)
    actual_host, actual_port = server.server_address[0], server.server_address[1]
    if verbose:
        print(f"trace_studio 运行中: http://{actual_host}:{actual_port}")
        print("按 Ctrl+C 停止。", flush=True)
    thread = threading.Thread(target=server.serve_forever, name="trace-http", daemon=True)
    thread.start()
    return server
