"""入口测试：``python -m trace_studio --port 0`` 实际启动并打印地址。"""

from __future__ import annotations

import json
import subprocess
import sys
import time
import unittest
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ModuleEntryTests(unittest.TestCase):
    def test_port_zero_prints_address_and_serves(self):
        proc = subprocess.Popen(
            [sys.executable, "-m", "trace_studio", "--port", "0"],
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        try:
            line = proc.stdout.readline()
            self.assertIn("http://", line)
            address = line.split("http://", 1)[1].strip()
            host, port_text = address.split(":")
            port = int(port_text)
            self.assertGreater(port, 0)

            # 服务确实可用：轮询状态接口
            deadline = time.time() + 5
            while True:
                try:
                    with urllib.request.urlopen(
                        f"http://{host}:{port}/api/state", timeout=1
                    ) as resp:
                        body = json.loads(resp.read().decode("utf-8"))
                    break
                except OSError:
                    if time.time() > deadline:
                        raise
                    time.sleep(0.1)
            self.assertTrue(body["empty"])
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
            if proc.stdout is not None:
                proc.stdout.close()

    def test_explicit_port_argument(self):
        proc = subprocess.Popen(
            [sys.executable, "-m", "trace_studio", "--port", "0",
             "--host", "127.0.0.1"],
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        try:
            line = proc.stdout.readline()
            self.assertIn("127.0.0.1:", line)
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
            if proc.stdout is not None:
                proc.stdout.close()


if __name__ == "__main__":
    unittest.main()
