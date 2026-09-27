"""Boot the service for real, POST one run, assert 200 and the output schema.

A smoke test that imports the app in-process proves the app object exists. This one
proves the *service* works: a process bound to a port, reached over HTTP, answering
with a payload that validates against ``RunOutput``.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx

from runtime.config import load_env_file
from runtime.schemas import HealthResponse, RunOutput

REPO_ROOT = Path(__file__).resolve().parent.parent
BOOT_TIMEOUT_S = 30.0
REQUEST_TIMEOUT_S = 30.0


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_for_health(client: httpx.Client, url: str, deadline: float) -> HealthResponse:
    last: str = "no attempt made"
    while time.monotonic() < deadline:
        try:
            response = client.get(url)
            if response.status_code == 200:
                return HealthResponse.model_validate(response.json())
            last = f"HTTP {response.status_code}: {response.text[:200]}"
        except httpx.HTTPError as exc:
            last = f"{type(exc).__name__}: {exc}"
        time.sleep(0.2)
    raise SystemExit(f"service did not become healthy within {BOOT_TIMEOUT_S}s ({last})")


def main() -> int:
    load_env_file()
    port = free_port()
    env = {
        **os.environ,
        "AGENT_HOST": "127.0.0.1",
        "AGENT_PORT": str(port),
        "AGENT_CONFIG": "default",
    }
    process = subprocess.Popen(
        [sys.executable, "-m", "service.app"],
        cwd=REPO_ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    base = f"http://127.0.0.1:{port}"

    try:
        with httpx.Client(timeout=REQUEST_TIMEOUT_S) as client:
            health = wait_for_health(client, f"{base}/healthz", time.monotonic() + BOOT_TIMEOUT_S)
            print(
                f"healthz: status={health.status} model_reachable={health.model_reachable} "
                f"tools_loaded={health.tools_loaded} version={health.version}"
            )

            response = client.post(
                f"{base}/run",
                json={"task": "What is 21 * 2?", "config": "default"},
            )
            if response.status_code != 200:
                raise SystemExit(
                    f"POST /run returned HTTP {response.status_code}: {response.text[:400]}"
                )

            output = RunOutput.model_validate(response.json())
            print(json.dumps(output.model_dump(mode="json"), indent=2, ensure_ascii=False))

            if output.status != "ok":
                raise SystemExit(f"expected status ok, got {output.status} ({output.reason})")
            if output.output != "21 * 2 is 42.":
                raise SystemExit(f"unexpected output: {output.output!r}")
            if not output.prompt_hashes:
                raise SystemExit("no prompt hashes recorded")

            print(
                f"smoke: ok — status={output.status} steps={output.steps} trace={output.trace_id}"
            )
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover
            process.kill()
        if process.returncode not in (0, -15, 143) and process.stdout is not None:
            print(process.stdout.read(), file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
