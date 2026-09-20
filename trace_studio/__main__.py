"""``python -m trace_studio`` 入口。"""

from __future__ import annotations

import argparse
import sys

from .server import build_server


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m trace_studio",
        description="本地单次调用追踪诊断工具（父子时序 / 错误定位 / 耗时归属）",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=0,
        help="监听端口；0（默认）表示由系统分配空闲端口并打印实际地址",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="绑定地址，默认仅本机 127.0.0.1",
    )
    args = parser.parse_args(argv)

    if not 0 <= args.port <= 65535:
        print(f"端口越界: {args.port}", file=sys.stderr)
        return 2

    server = build_server(args.host, args.port)
    actual_host, actual_port = server.server_address
    print(f"trace_studio 运行中: http://{actual_host}:{actual_port}")
    print("按 Ctrl+C 停止。", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n正在停止...")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
