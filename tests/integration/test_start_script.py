from __future__ import annotations

import os
import socket
import subprocess
import time
from pathlib import Path
from urllib.request import urlopen


PROJECT_ROOT = Path(__file__).resolve().parents[2]
START_SCRIPT = PROJECT_ROOT / "start.sh"


def _unused_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_start_script_serves_dashboard_from_another_working_directory(tmp_path: Path) -> None:
    assert START_SCRIPT.is_file()
    assert os.access(START_SCRIPT, os.X_OK)
    subprocess.run(["bash", "-n", str(START_SCRIPT)], check=True)

    port = _unused_port()
    process = subprocess.Popen(
        [str(START_SCRIPT)],
        cwd=tmp_path,
        env={**os.environ, "IQ_RADAR_PORT": str(port)},
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        # The startup path runs `npm run build` (≈30s on a cold build) before
        # the server binds, so a 30s budget was too tight to be reliable.
        deadline = time.monotonic() + 120
        while True:
            if process.poll() is not None:
                output = process.stdout.read() if process.stdout else ""
                raise AssertionError(f"start.sh exited early:\n{output}")
            try:
                with urlopen(f"http://127.0.0.1:{port}/health/readiness", timeout=1) as response:
                    assert response.status == 200
                    break
            except OSError:
                if time.monotonic() >= deadline:
                    raise AssertionError("start.sh did not become ready within 120 seconds")
                time.sleep(0.5)

        with urlopen(f"http://127.0.0.1:{port}/", timeout=2) as response:
            assert response.status == 200
            assert b"IQRadar" in response.read()

        with urlopen(f"http://127.0.0.1:{port}/docs", timeout=2) as response:
            assert response.status == 200

        with urlopen(f"http://127.0.0.1:{port}/openapi.json", timeout=2) as response:
            assert response.status == 200
    finally:
        process.terminate()
        process.wait(timeout=10)
