"""Starts the isolated browser runner (runner/worker.py) and talks to it.

Protocol: one JSON object per line.
  app -> runner: {"id": 1, "cmd": "open", "args": {...}}
  runner -> app: {"id": 1, "ok": true, "data": {...}}  or  {"ok": false, "error": "..."}
"""
import json
import os
import queue
import subprocess
import sys
import threading
import time

from app.config import DATA_DIR, SCREENSHOT_DIR, WORKER_PATH, settings

SECRET_WORDS = ("KEY", "TOKEN", "SECRET", "PASSWORD")


class RunnerError(Exception):
    pass


def safe_environment() -> dict:
    """Copy of the environment WITHOUT secrets (e.g. GROQ_API_KEY)."""
    env = {}
    for name, value in os.environ.items():
        if any(word in name.upper() for word in SECRET_WORDS):
            continue
        env[name] = value
    return env


class RunnerClient:
    def __init__(self, timeout_seconds: int):
        self.timeout_seconds = timeout_seconds
        self.process = None
        self.lines = None
        self.stderr_file = None
        self.next_id = 0
        self.lock = threading.Lock()  # one command at a time

    def _start(self):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
        self.stderr_file = open(DATA_DIR / "runner_stderr.log", "a", encoding="utf-8")
        self.process = subprocess.Popen(
            [sys.executable, "-u", str(WORKER_PATH)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self.stderr_file,
            cwd=str(DATA_DIR),
            env=safe_environment(),
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
        self.lines = queue.Queue()
        reader = threading.Thread(target=self._read_output, args=(self.process, self.lines), daemon=True)
        reader.start()
        self._send("configure", {
            "allowed_hosts": settings.allowed_hosts,
            "screenshot_dir": str(SCREENSHOT_DIR),
        }, timeout=30)

    @staticmethod
    def _read_output(process, lines):
        for line in process.stdout:
            lines.put(line)
        lines.put(None)  # signals that the runner exited

    def _send(self, command: str, args: dict, timeout: float):
        self.next_id += 1
        message_id = self.next_id
        try:
            self.process.stdin.write(json.dumps({"id": message_id, "cmd": command, "args": args}) + "\n")
            self.process.stdin.flush()
        except OSError as exc:
            self._kill()
            raise RunnerError(f"Could not talk to the runner: {exc}")

        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self._kill()
                raise RunnerError(f"Runner timed out after {timeout} s on '{command}' (runner restarted)")
            try:
                line = self.lines.get(timeout=remaining)
            except queue.Empty:
                continue
            if line is None:
                self.process = None
                raise RunnerError("Runner process exited. See data/runner_stderr.log")
            try:
                reply = json.loads(line)
            except json.JSONDecodeError:
                continue  # ignore any non-protocol output
            if reply.get("id") != message_id:
                continue
            if not reply.get("ok"):
                raise RunnerError(reply.get("error") or "Unknown runner error")
            return reply.get("data")

    def call(self, command: str, args: dict = None, timeout: float = None):
        with self.lock:
            if self.process is None or self.process.poll() is not None:
                self._start()
            return self._send(command, args or {}, timeout or self.timeout_seconds)

    def _kill(self):
        if self.process is not None:
            try:
                self.process.kill()
            except Exception:
                pass
        self.process = None

    def stop(self):
        with self.lock:
            if self.process is not None and self.process.poll() is None:
                try:
                    self.process.stdin.write(json.dumps({"id": 0, "cmd": "shutdown"}) + "\n")
                    self.process.stdin.flush()
                    self.process.wait(timeout=10)
                except Exception:
                    self._kill()
            self.process = None
            if self.stderr_file is not None:
                self.stderr_file.close()
                self.stderr_file = None


runner = RunnerClient(settings.runner_timeout)
