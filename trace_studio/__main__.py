"""``python -m trace_studio`` 入口。

支持 --port（含 0 表示由系统分配端口）与 --host；启动后打印实际地址。
"""

from __future__ import annotations

import argparse
import re
import sys

from .server import serve


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m trace_studio",
        description="本地追踪诊断网页（仅标准库）",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=0,
        help="监听端口；0（默认）表示由操作系统分配空闲端口",
    )
    parser.add_argument("--host", default="127.0.0.1", help="监听地址，默认 127.0.0.1")
    args = parser.parse_args(argv)

    if not 0 <= args.port <= 65535:
        print(f"非法端口：{args.port}", file=sys.stderr)
        return 2
    if not re.fullmatch(r"[\w.\-:]+", args.host):
        print(f"非法 host：{args.host}", file=sys.stderr)
        return 2

    serve(args.port, args.host)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
