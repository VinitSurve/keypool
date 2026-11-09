"""Gateway process management, daemon control, and server lifecycle."""

from __future__ import annotations

import argparse
import contextlib
import os
import signal
import subprocess
import sys
import time
from typing import Any

import httpx
import uvicorn

from keypool.cli.formatting import RuntimeContext
from keypool.gateway.app import create_gateway_app
from keypool.gateway.auth import GatewayAuth


class GatewayServer:
    """Manages the background daemon lifecycle of the Local AI Gateway."""

    def __init__(self, ctx: RuntimeContext | None = None) -> None:
        self.ctx = ctx or RuntimeContext()
        self.paths = self.ctx.paths
        self.auth = GatewayAuth(self.paths)

    def is_running(self) -> tuple[bool, int | None]:
        """Check if the gateway process is currently active."""
        if not self.paths.gateway_pid_path.exists():
            return False, None

        try:
            pid_str = self.paths.gateway_pid_path.read_text(encoding="utf-8").strip()
            pid = int(pid_str)
        except (ValueError, OSError):
            self.paths.gateway_pid_path.unlink(missing_ok=True)
            return False, None

        try:
            # Signal 0 tests process existence without killing it
            os.kill(pid, 0)
            return True, pid
        except OSError:
            # Process does not exist, clean up stale PID file
            self.paths.gateway_pid_path.unlink(missing_ok=True)
            return False, None

    def start(
        self,
        *,
        host: str = "127.0.0.1",
        port: int = 8765,
        daemon: bool = False,
        require_auth: bool = True,
    ) -> dict[str, Any]:
        """Start the Local AI Gateway in foreground or daemon mode."""
        running, pid = self.is_running()
        if running:
            return {
                "status": "already_running",
                "pid": pid,
                "host": host,
                "port": port,
                "token": self.auth.get_or_create_token() if require_auth else None,
            }

        self.paths.ensure_directories()
        token = self.auth.get_or_create_token() if require_auth else None

        if daemon:
            # Spawn detached daemon subprocess
            cmd = [
                sys.executable,
                "-m",
                "keypool.gateway.server",
                "--host",
                host,
                "--port",
                str(port),
            ]
            if not require_auth:
                cmd.append("--no-auth")

            with open(self.paths.gateway_log_path, "a", encoding="utf-8") as log_f:
                proc = subprocess.Popen(
                    cmd,
                    stdout=log_f,
                    stderr=log_f,
                    start_new_session=True,
                    close_fds=True,
                )

            self.paths.gateway_pid_path.write_text(str(proc.pid), encoding="utf-8")

            # Poll for health check readiness up to 3 seconds
            ready = False
            for _ in range(30):
                time.sleep(0.1)
                try:
                    resp = httpx.get(f"http://{host}:{port}/health", timeout=0.5)
                    if resp.status_code == 200:
                        ready = True
                        break
                except httpx.RequestError:
                    continue

            return {
                "status": "started" if ready else "launching",
                "pid": proc.pid,
                "host": host,
                "port": port,
                "token": token,
                "log_file": str(self.paths.gateway_log_path),
            }
        else:
            # Run in foreground
            app = create_gateway_app(self.ctx, require_auth=require_auth)
            # Write current PID
            self.paths.gateway_pid_path.write_text(str(os.getpid()), encoding="utf-8")
            try:
                uvicorn.run(app, host=host, port=port, log_level="info")
            finally:
                self.paths.gateway_pid_path.unlink(missing_ok=True)
            return {"status": "stopped"}

    def stop(self) -> dict[str, Any]:
        """Stop the running Local AI Gateway process."""
        running, pid = self.is_running()
        if not running or pid is None:
            return {"status": "not_running"}

        with contextlib.suppress(OSError):
            os.kill(pid, signal.SIGTERM)
            # Wait up to 3 seconds for graceful exit
            for _ in range(30):
                time.sleep(0.1)
                try:
                    os.kill(pid, 0)
                except OSError:
                    break
            else:
                # Force kill if still alive
                with contextlib.suppress(OSError):
                    os.kill(pid, signal.SIGKILL)

        self.paths.gateway_pid_path.unlink(missing_ok=True)
        return {"status": "stopped", "pid": pid}

    def status(self) -> dict[str, Any]:
        """Retrieve live status and metrics from the gateway."""
        running, pid = self.is_running()
        if not running or pid is None:
            return {"status": "stopped", "running": False}

        host = self.ctx.settings.gateway.host
        port = self.ctx.settings.gateway.port

        health_data: dict[str, Any] = {}
        try:
            resp = httpx.get(f"http://{host}:{port}/health", timeout=1.0)
            if resp.status_code == 200:
                health_data = resp.json()
        except httpx.RequestError:
            pass

        return {
            "status": "running",
            "running": True,
            "pid": pid,
            "host": host,
            "port": port,
            "health": health_data,
            "token": self.auth.get_or_create_token(),
        }


def main() -> None:
    """Entry point for running gateway server directly."""
    parser = argparse.ArgumentParser(description="Keypool Local AI Gateway Server")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind")
    parser.add_argument("--port", type=int, default=8765, help="Port to listen on")
    parser.add_argument("--no-auth", action="store_true", help="Disable bearer authentication")

    args = parser.parse_args()
    ctx = RuntimeContext()
    app = create_gateway_app(ctx, require_auth=not args.no_auth)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
