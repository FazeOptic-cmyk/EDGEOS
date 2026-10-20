from __future__ import annotations

import asyncio
import os
from collections.abc import Mapping
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from edgeos.desktop import (
    DEFAULT_PORT,
    LOOPBACK_HOST,
    READINESS_PATH,
    SHUTDOWN_PATH,
    DesktopOptions,
    configure_desktop_environment,
    create_server,
    parse_options,
)
from edgeos import desktop


async def _request(
    application: FastAPI,
    method: str,
    path: str,
    *,
    headers: Mapping[str, str] | None = None,
    base_url: str = "http://127.0.0.1",
) -> httpx.Response:
    transport = httpx.ASGITransport(app=application)
    async with httpx.AsyncClient(transport=transport, base_url=base_url) as client:
        return await client.request(method, path, headers=headers)


def test_desktop_defaults_are_demo_and_loopback_only() -> None:
    environment = {"EDGEOS_DESKTOP_API_TOKEN": "api-" + "a" * 32}
    configure_desktop_environment(environment)
    options = parse_options([], environment)
    application = FastAPI()
    server = create_server(options, application)

    assert environment["EDGEOS_ENV"] == "desktop"
    assert environment["EDGEOS_MODE"] == "demo"
    assert environment["EDGEOS_CORS_ORIGINS"] == "http://127.0.0.1:3210"
    assert options.port == DEFAULT_PORT
    assert options.api_token == environment["EDGEOS_DESKTOP_API_TOKEN"]
    assert server.config.host == LOOPBACK_HOST
    assert server.config.port == DEFAULT_PORT
    assert server.config.workers == 1
    assert server.config.access_log is False


def test_desktop_forces_demo_environment_and_accepts_cli_port() -> None:
    environment = {
        "EDGEOS_ENV": "test-desktop",
        "EDGEOS_MODE": "live",
        "EDGEOS_CORS_ORIGINS": "http://127.0.0.1:3210",
        "EDGEOS_DESKTOP_API_PORT": "9000",
        "EDGEOS_DESKTOP_API_TOKEN": "api-" + "a" * 32,
    }
    configure_desktop_environment(environment)
    options = parse_options(["--port", "8766", "--log-level", "info"], environment)

    assert environment["EDGEOS_ENV"] == "desktop"
    assert environment["EDGEOS_MODE"] == "demo"
    assert environment["EDGEOS_CORS_ORIGINS"] == "http://127.0.0.1:3210"
    assert options.port == 8766
    assert options.log_level == "info"


@pytest.mark.parametrize("value", ["abc", "0", "80", "65536"])
def test_desktop_rejects_invalid_environment_port(value: str) -> None:
    with pytest.raises(SystemExit) as error:
        parse_options([], {"EDGEOS_DESKTOP_API_PORT": value})

    assert error.value.code == 2


def test_desktop_rejects_invalid_environment_log_level() -> None:
    with pytest.raises(SystemExit) as error:
        parse_options([], {"EDGEOS_DESKTOP_LOG_LEVEL": "verbose"})

    assert error.value.code == 2


def test_desktop_shutdown_endpoint_is_absent_without_token() -> None:
    application = FastAPI()
    create_server(DesktopOptions(DEFAULT_PORT, "warning", None), application)

    response = asyncio.run(_request(application, "POST", SHUTDOWN_PATH))
    assert response.status_code == 404


def test_desktop_shutdown_requires_token_and_sets_graceful_exit() -> None:
    token = "a" * 32
    application = FastAPI()
    server = create_server(DesktopOptions(DEFAULT_PORT, "warning", token), application)

    missing = asyncio.run(_request(application, "POST", SHUTDOWN_PATH))
    incorrect = asyncio.run(
        _request(
            application,
            "POST",
            SHUTDOWN_PATH,
            headers={"x-edgeos-desktop-token": "b" * 32},
        )
    )
    response = asyncio.run(
        _request(
            application,
            "POST",
            SHUTDOWN_PATH,
            headers={"x-edgeos-desktop-token": token},
        )
    )

    assert missing.status_code == 403
    assert incorrect.status_code == 403
    assert response.status_code == 200
    assert response.json() == {"status": "shutting_down"}
    assert server.should_exit is True


def test_desktop_readiness_returns_exact_instance_nonce_without_caching() -> None:
    nonce = "desktop-instance-" + "c" * 32
    application = FastAPI()
    create_server(
        DesktopOptions(DEFAULT_PORT, "warning", None, instance_nonce=nonce),
        application,
    )

    response = asyncio.run(_request(application, "GET", READINESS_PATH))

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "status": "ok",
        "service": "edgeos-api",
        "instance_nonce": nonce,
    }


def test_desktop_rejects_untrusted_host_header() -> None:
    application = FastAPI()
    create_server(DesktopOptions(DEFAULT_PORT, "warning", None), application)

    response = asyncio.run(
        _request(application, "GET", "/", base_url="http://attacker.example")
    )

    assert response.status_code == 400


@pytest.mark.parametrize(
    "environment",
    [
        {"EDGEOS_DESKTOP_SHUTDOWN_TOKEN": "too-short"},
        {"EDGEOS_DESKTOP_API_TOKEN": "too-short"},
        {"EDGEOS_DESKTOP_INSTANCE_NONCE": "too-short"},
        {"EDGEOS_DESKTOP_PARENT_PID": "not-a-pid"},
        {"EDGEOS_DESKTOP_PARENT_PID": "0"},
    ],
)
def test_desktop_rejects_invalid_private_runtime_values(environment: dict[str, str]) -> None:
    with pytest.raises(SystemExit) as error:
        parse_options([], environment)

    assert error.value.code == 2


def test_desktop_accepts_parent_pid_from_environment() -> None:
    parent_pid = os.getpid() + 100_000
    options = parse_options([], {"EDGEOS_DESKTOP_PARENT_PID": str(parent_pid)})

    assert options.parent_pid == parent_pid


def test_desktop_runtime_requires_per_launch_api_token() -> None:
    with pytest.raises(SystemExit) as error:
        parse_options([], {"EDGEOS_ENV": "desktop"})

    assert error.value.code == 2


def test_desktop_api_token_protects_business_routes_but_not_readiness() -> None:
    api_token = "api-" + "a" * 32
    nonce = "desktop-instance-" + "c" * 32
    application = FastAPI()

    @application.get("/api/v1/private")
    async def private_route() -> dict[str, str]:
        return {"status": "ok"}

    create_server(
        DesktopOptions(
            DEFAULT_PORT,
            "warning",
            None,
            api_token=api_token,
            instance_nonce=nonce,
        ),
        application,
    )

    missing = asyncio.run(_request(application, "GET", "/api/v1/private"))
    incorrect = asyncio.run(
        _request(
            application,
            "GET",
            "/api/v1/private",
            headers={"x-edgeos-desktop-token": "api-" + "b" * 32},
        )
    )
    accepted = asyncio.run(
        _request(
            application,
            "GET",
            "/api/v1/private",
            headers={"x-edgeos-desktop-token": api_token},
        )
    )
    readiness = asyncio.run(_request(application, "GET", READINESS_PATH))

    assert missing.status_code == 403
    assert missing.headers["cache-control"] == "no-store"
    assert incorrect.status_code == 403
    assert accepted.status_code == 200
    assert readiness.status_code == 200


def test_parent_monitor_requests_server_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    observed: list[int] = []
    server = SimpleNamespace(should_exit=False)
    monkeypatch.setattr(desktop, "_wait_for_parent_exit", observed.append)

    thread = desktop._start_parent_monitor(4242, server)  # type: ignore[arg-type]
    thread.join(timeout=1)

    assert not thread.is_alive()
    assert observed == [4242]
    assert server.should_exit is True
