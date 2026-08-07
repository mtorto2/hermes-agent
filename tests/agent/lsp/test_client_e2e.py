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

from agent.lsp.client import LSPClient, SHUTDOWN_GRACE


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








