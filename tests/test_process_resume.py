"""Actual CLI process death and restart; no external networking or models."""

import os
import socket
import subprocess
import sys
import time

import httpx


def wait_for(check, process, seconds=20):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        assert process.poll() is None, "CLI exited unexpectedly"
        try:
            result = check()
            if result:
                return result
        except (httpx.TransportError, KeyError, IndexError):
            pass
        time.sleep(0.1)
    raise AssertionError("Timed out waiting for CLI")


def test_kill_and_resume_cli(tmp_path):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = {**os.environ, "META_API_KEY": "fake-secret-never-log", "PYTHONPATH": "src"}
    common = ["--workspace", str(tmp_path), "--port", str(port), "--no-open-browser"]
    log_path = tmp_path / "process.log"
    with log_path.open("w+") as log, httpx.Client(base_url=f"http://127.0.0.1:{port}") as client:
        process = subprocess.Popen(
            [sys.executable, "-m", "spriteforge", "start", "--mock", *common],
            env=env,
            stdout=log,
            stderr=log,
        )
        try:
            run = wait_for(lambda: client.get("/api/runs").json()["runs"][0], process)
            id = run["id"]
            partial = wait_for(
                lambda: (
                    d if (d := client.get(f"/api/runs/{id}").json())["run"]["cursor"] >= 4 else None
                ),
                process,
            )
            assert partial["run"]["cursor"] < 10
            first_artifact = partial["artifacts"][0]
        finally:
            process.kill()
            process.wait(timeout=10)
        process = subprocess.Popen(
            [sys.executable, "-m", "spriteforge", "resume", id, *common],
            env=env,
            stdout=log,
            stderr=log,
        )
        try:
            complete = wait_for(
                lambda: (
                    d
                    if (d := client.get(f"/api/runs/{id}").json())["run"]["status"] == "completed"
                    else None
                ),
                process,
            )
            assert complete["run"]["cursor"] == 10
            assert len(complete["artifacts"]) == 2
            assert first_artifact in complete["artifacts"]
            assert client.get("/api/artifacts/" + first_artifact["id"]).status_code == 200
            events = client.get(f"/api/runs/{id}/events").text
            assert events.count('"type":"run.completed"') == 1
            assert events.count('"type":"cost.reserved"') == 3
        finally:
            process.kill()
            process.wait(timeout=10)
    assert "fake-secret-never-log" not in log_path.read_text()
