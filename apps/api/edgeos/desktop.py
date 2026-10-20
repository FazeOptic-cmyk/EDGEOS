"""Windows desktop entry point for the bundled EDGEOS research API.

This module deliberately has a small import surface.  In particular, the FastAPI
application is imported only after desktop-safe environment defaults are applied.
That matters because ``edgeos.config`` snapshots environment values at import
time and because PyInstaller executes this module as the frozen application.
"""

from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import hmac
import multiprocessing
import os
import sys
import threading
import time
from collections.abc import Mapping, MutableMapping, Sequence
from dataclasses import dataclass

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send


LOOPBACK_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
SHUTDOWN_PATH = "/api/v1/desktop/shutdown"
READINESS_PATH = "/api/v1/desktop/readiness"
_MINIMUM_SHUTDOWN_TOKEN_LENGTH = 32
_MINIMUM_API_TOKEN_LENGTH = 32
_MINIMUM_INSTANCE_NONCE_LENGTH = 32
_LOG_LEVELS = ("critical", "error", "warning", "info", "debug", "trace")


@dataclass(frozen=True, slots=True)
class DesktopOptions:
    """Validated options for the single-process desktop API."""

    port: int
    log_level: str
    shutdown_token: str | None
    api_token: str | None = None
    instance_nonce: str | None = None
    parent_pid: int | None = None


def _port(value: str | int) -> int:
    """Parse a non-privileged TCP port for the loopback listener."""
    try:
        port = int(value)
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError("port must be an integer") from exc
    if not 1024 <= port <= 65535:
        raise argparse.ArgumentTypeError("port must be between 1024 and 65535")
    return port


def configure_desktop_environment(environment: MutableMapping[str, str]) -> None:
    """Force the bundled API's non-live safety boundary."""
    environment["EDGEOS_ENV"] = "desktop"
    environment["EDGEOS_MODE"] = "demo"
    environment.setdefault("EDGEOS_CORS_ORIGINS", "http://127.0.0.1:3210")


def parse_options(
    arguments: Sequence[str] | None = None,
    environment: Mapping[str, str] | None = None,
) -> DesktopOptions:
    """Read public CLI options and keep the shutdown secret out of process args."""
    values = os.environ if environment is None else environment
    parser = argparse.ArgumentParser(
        prog="edgeos-api",
        description="Local EDGEOS paper-research API (loopback only)",
    )
    parser.add_argument(
        "--port",
        type=_port,
        default=None,
        help=f"loopback port (default: {DEFAULT_PORT}; env: EDGEOS_DESKTOP_API_PORT)",
    )
    configured_log_level = values.get("EDGEOS_DESKTOP_LOG_LEVEL", "warning").strip().lower()
    if configured_log_level not in _LOG_LEVELS:
        parser.error(
            "EDGEOS_DESKTOP_LOG_LEVEL must be one of " + ", ".join(_LOG_LEVELS)
        )
    parser.add_argument(
        "--log-level",
        choices=_LOG_LEVELS,
        default=configured_log_level,
        help="backend log verbosity (env: EDGEOS_DESKTOP_LOG_LEVEL)",
    )
    namespace = parser.parse_args(arguments)

    raw_port = namespace.port
    if raw_port is None:
        raw_port = values.get("EDGEOS_DESKTOP_API_PORT", str(DEFAULT_PORT))
        try:
            raw_port = _port(raw_port)
        except argparse.ArgumentTypeError as exc:
            parser.error(f"EDGEOS_DESKTOP_API_PORT: {exc}")

    shutdown_token = values.get("EDGEOS_DESKTOP_SHUTDOWN_TOKEN") or None
    if shutdown_token is not None and len(shutdown_token) < _MINIMUM_SHUTDOWN_TOKEN_LENGTH:
        parser.error(
            "EDGEOS_DESKTOP_SHUTDOWN_TOKEN must contain at least "
            f"{_MINIMUM_SHUTDOWN_TOKEN_LENGTH} characters"
        )

    api_token = values.get("EDGEOS_DESKTOP_API_TOKEN") or None
    if api_token is not None and len(api_token) < _MINIMUM_API_TOKEN_LENGTH:
        parser.error(
            "EDGEOS_DESKTOP_API_TOKEN must contain at least "
            f"{_MINIMUM_API_TOKEN_LENGTH} characters"
        )
    if values.get("EDGEOS_ENV") == "desktop" and api_token is None:
        parser.error("EDGEOS_DESKTOP_API_TOKEN is required in the desktop runtime")

    instance_nonce = values.get("EDGEOS_DESKTOP_INSTANCE_NONCE") or None
    if instance_nonce is not None and len(instance_nonce) < _MINIMUM_INSTANCE_NONCE_LENGTH:
        parser.error(
            "EDGEOS_DESKTOP_INSTANCE_NONCE must contain at least "
            f"{_MINIMUM_INSTANCE_NONCE_LENGTH} characters"
        )

    raw_parent_pid = values.get("EDGEOS_DESKTOP_PARENT_PID")
    parent_pid: int | None = None
    if raw_parent_pid:
        try:
            parent_pid = int(raw_parent_pid)
        except ValueError:
            parser.error("EDGEOS_DESKTOP_PARENT_PID must be a positive integer")
        if parent_pid <= 0 or parent_pid == os.getpid():
            parser.error("EDGEOS_DESKTOP_PARENT_PID must identify another process")

    return DesktopOptions(
        port=raw_port,
        log_level=namespace.log_level,
        shutdown_token=shutdown_token,
        api_token=api_token,
        instance_nonce=instance_nonce,
        parent_pid=parent_pid,
    )


class DesktopApiTokenMiddleware:
    """Authenticate loopback business requests with a per-launch secret.

    Loopback and random ports reduce exposure but are not authentication. The
    token is passed only between owned Electron child processes and is never
    exposed to the renderer bundle or command line.
    """

    def __init__(self, app: ASGIApp, token: str) -> None:
        self.app = app
        self.expected = token.encode("utf-8")
        self.exempt_paths = frozenset({READINESS_PATH, SHUTDOWN_PATH})

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("path") in self.exempt_paths:
            await self.app(scope, receive, send)
            return
        presented = dict(scope.get("headers", ())).get(
            b"x-edgeos-desktop-token", b""
        )
        candidate = (
            presented
            if len(presented) == len(self.expected)
            else bytes(len(self.expected))
        )
        if not hmac.compare_digest(candidate, self.expected):
            response = JSONResponse(
                {"detail": "Desktop API authentication failed"},
                status_code=403,
                headers={"Cache-Control": "no-store"},
            )
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)


def _install_shutdown_route(application: FastAPI, server: uvicorn.Server, token: str) -> None:
    """Install a secret-protected local endpoint for orderly desktop shutdown."""

    async def shutdown(request: Request) -> dict[str, str]:
        presented = request.headers.get("x-edgeos-desktop-token")
        if presented is None or not hmac.compare_digest(presented, token):
            raise HTTPException(status_code=403, detail="Invalid desktop shutdown token")
        # Uvicorn observes this flag after completing the current response and
        # then runs the FastAPI lifespan shutdown before exiting.
        server.should_exit = True
        return {"status": "shutting_down"}

    application.add_api_route(
        SHUTDOWN_PATH,
        shutdown,
        methods=["POST"],
        include_in_schema=False,
        name="desktop_shutdown",
    )


def _install_readiness_route(application: FastAPI, nonce: str) -> None:
    """Prove that Electron is talking to the API process it just launched."""

    async def readiness() -> JSONResponse:
        return JSONResponse(
            {
                "status": "ok",
                "service": "edgeos-api",
                "instance_nonce": nonce,
            },
            headers={"Cache-Control": "no-store"},
        )

    application.add_api_route(
        READINESS_PATH,
        readiness,
        methods=["GET"],
        include_in_schema=False,
        name="desktop_readiness",
    )


def _wait_for_parent_exit(parent_pid: int) -> None:
    """Block a daemon monitor until the owning Electron process exits."""
    if sys.platform == "win32":
        synchronize = 0x00100000
        infinite = 0xFFFFFFFF
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel32.WaitForSingleObject.restype = wintypes.DWORD
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        handle = kernel32.OpenProcess(synchronize, False, parent_pid)
        if not handle:
            return
        try:
            kernel32.WaitForSingleObject(handle, infinite)
        finally:
            kernel32.CloseHandle(handle)
        return

    while True:
        try:
            os.kill(parent_pid, 0)
        except ProcessLookupError:
            return
        except PermissionError:
            # The process still exists, even if this account cannot signal it.
            pass
        time.sleep(1)


def _start_parent_monitor(parent_pid: int, server: uvicorn.Server) -> threading.Thread:
    def monitor() -> None:
        _wait_for_parent_exit(parent_pid)
        server.should_exit = True

    thread = threading.Thread(target=monitor, name="edgeos-parent-monitor", daemon=True)
    thread.start()
    return thread


def create_server(options: DesktopOptions, application: FastAPI | None = None) -> uvicorn.Server:
    """Create the frozen-app server with a host that cannot be broadened by CLI."""
    if application is None:
        # Import after ``configure_desktop_environment`` in ``main`` so Settings
        # observes demo-safe desktop defaults on first import.
        from edgeos.main import app as edgeos_app

        application = edgeos_app

    application.add_middleware(TrustedHostMiddleware, allowed_hosts=[LOOPBACK_HOST])
    if options.api_token is not None:
        application.add_middleware(DesktopApiTokenMiddleware, token=options.api_token)

    configuration = uvicorn.Config(
        app=application,
        host=LOOPBACK_HOST,
        port=options.port,
        workers=1,
        loop="asyncio",
        http="h11",
        ws="none",
        lifespan="on",
        access_log=False,
        log_level=options.log_level,
        use_colors=False,
        timeout_graceful_shutdown=10,
    )
    server = uvicorn.Server(configuration)
    if options.shutdown_token is not None:
        _install_shutdown_route(application, server, options.shutdown_token)
    if options.instance_nonce is not None:
        _install_readiness_route(application, options.instance_nonce)
    return server


def main(arguments: Sequence[str] | None = None) -> int:
    """Run the local API until a signal or authenticated shutdown request."""
    configure_desktop_environment(os.environ)
    options = parse_options(arguments)
    server = create_server(options)
    if options.parent_pid is not None:
        _start_parent_monitor(options.parent_pid, server)
    server.run()
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised by packaged smoke tests
    multiprocessing.freeze_support()
    raise SystemExit(main())
