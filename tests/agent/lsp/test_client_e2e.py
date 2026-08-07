"""End-to-end client tests against the in-process mock LSP server.

Spins up :file:`_mock_lsp_server.py` as an actual subprocess, drives
it through real LSP traffic, and asserts diagnostic flow.  This is
the closest thing we have to integration coverage without requiring
pyright/gopls/etc. to be installed in CI.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest

from agent.lsp.client import (
    GRACEFUL_SHUTDOWN_TIMEOUT,
    LSPClient,
    SHUTDOWN_GRACE,
    SHUTDOWN_REQUEST_TIMEOUT,
)


MOCK_SERVER = str(Path(__file__).parent / "_mock_lsp_server.py")


def _client(
    workspace: Path,
    script: str = "clean",
    *,
    exit_delay: float = 0,
) -> LSPClient:
    env = {"MOCK_LSP_SCRIPT": script, "PYTHONPATH": os.environ.get("PYTHONPATH", "")}
    if exit_delay > 0:
        env["MOCK_LSP_EXIT_DELAY"] = str(exit_delay)
    return LSPClient(
        server_id=f"mock-{script}",
        workspace_root=str(workspace),
        command=[sys.executable, MOCK_SERVER],
        env=env,
        cwd=str(workspace),
    )


@pytest.mark.asyncio
async def test_client_lifecycle_clean(tmp_path: Path, monkeypatch):
    """Full lifecycle: spawn, initialize, open, get clean diagnostics, shutdown."""
    f = tmp_path / "x.py"
    f.write_text("print('hi')\n")

    client = _client(tmp_path, "clean")
    await client.start()
    terminate_calls = []
    original_terminate = client._proc.terminate

    def _record_terminate():
        terminate_calls.append(True)
        return original_terminate()

    monkeypatch.setattr(client._proc, "terminate", _record_terminate)
    try:
        assert client.is_running
        version = await client.open_file(str(f), language_id="python")
        assert version == 0
        await client.wait_for_diagnostics(str(f), version, mode="document")
        diags = client.diagnostics_for(str(f))
        assert diags == []
    finally:
        await client.shutdown()
    assert not client.is_running
    assert terminate_calls == []


@pytest.mark.asyncio
async def test_client_lifecycle_allows_clean_exit_beyond_force_stop_grace(tmp_path: Path, monkeypatch):
    """A clean server gets a cooperative-exit budget before termination starts."""
    client = _client(tmp_path, "clean", exit_delay=SHUTDOWN_GRACE + 0.1)
    await client.start()
    terminate_calls = []
    original_terminate = client._proc.terminate

    def _record_terminate():
        terminate_calls.append(True)
        return original_terminate()

    monkeypatch.setattr(client._proc, "terminate", _record_terminate)

    await client.shutdown()

    assert not client.is_running
    assert terminate_calls == []


@pytest.mark.asyncio
async def test_client_terminates_server_ignoring_shutdown_and_exit(tmp_path: Path, monkeypatch):
    """A protocol-unresponsive server is terminated and reaped."""
    client = _client(tmp_path, "ignore_shutdown")
    await client.start()
    proc = client._proc
    assert proc is not None
    terminate_calls = []
    original_terminate = proc.terminate

    def _record_terminate():
        terminate_calls.append(True)
        return original_terminate()

    monkeypatch.setattr(proc, "terminate", _record_terminate)

    await client.shutdown()

    assert not client.is_running
    assert terminate_calls == [True]
    assert proc.returncode is not None


@pytest.mark.asyncio
async def test_client_teardown_does_not_wait_for_backpressured_exit_drain(tmp_path: Path, monkeypatch):
    """Exit flow control must not delay fallback teardown of a wedged server."""
    client = _client(tmp_path, "ignore_shutdown")
    await client.start()
    proc = client._proc
    assert proc is not None
    assert proc.stdin is not None
    original_drain = proc.stdin.drain
    drain_calls = 0
    blocked_drain = asyncio.Event()

    async def _backpressured_exit_drain():
        nonlocal drain_calls
        drain_calls += 1
        if drain_calls == 1:
            await original_drain()
            return
        await blocked_drain.wait()

    monkeypatch.setattr(proc.stdin, "drain", _backpressured_exit_drain)
    shutdown_task = asyncio.create_task(client.shutdown())
    try:
        await asyncio.sleep(
            SHUTDOWN_REQUEST_TIMEOUT + GRACEFUL_SHUTDOWN_TIMEOUT + SHUTDOWN_GRACE + 0.5
        )
        assert shutdown_task.done(), "exit drain blocked process termination fallback"
        await shutdown_task
        assert not client.is_running
        assert proc.returncode is not None
    finally:
        if not shutdown_task.done():
            shutdown_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await shutdown_task


@pytest.mark.asyncio
async def test_client_receives_published_errors(tmp_path: Path):
    f = tmp_path / "x.py"
    f.write_text("print('hi')\n")

    client = _client(tmp_path, "errors")
    await client.start()
    try:
        version = await client.open_file(str(f), language_id="python")
        await client.wait_for_diagnostics(str(f), version, mode="document")
        diags = client.diagnostics_for(str(f))
        assert len(diags) == 1
        d = diags[0]
        assert d["severity"] == 1
        assert d["code"] == "MOCK001"
        assert d["source"] == "mock-lsp"
        assert "synthetic error" in d["message"]
    finally:
        await client.shutdown()








