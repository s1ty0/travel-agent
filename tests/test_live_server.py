"""Exercise the installed server over a real loopback socket, with no external APIs."""

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

import pytest


@pytest.mark.smoke
def test_real_http_server(tmp_path):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    # Disable proxy discovery for this local smoke test only.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    base = f"http://127.0.0.1:{port}"
    with (tmp_path / "server.log").open("w+", encoding="utf-8") as log:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "travel_agent.api:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            ],
            stdout=log,
            stderr=log,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )
        try:
            deadline = time.monotonic() + 15
            while True:
                try:
                    with opener.open(base + "/health", timeout=1) as response:
                        assert json.load(response)["stage"] == "M1"
                    break
                except (urllib.error.URLError, TimeoutError):
                    if process.poll() is not None or time.monotonic() > deadline:
                        log.seek(0)
                        pytest.fail(f"Server failed to start: {log.read()}")
                    time.sleep(0.1)
            request = urllib.request.Request(
                base + "/v1/intake",
                data=json.dumps({"destination": "南京附近", "budget": "5000以内"}).encode(),
                headers={"Content-Type": "application/json"},
            )
            with opener.open(request, timeout=3) as response:
                assert json.load(response)["budget"]["maximum_minor"] == 500000
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
