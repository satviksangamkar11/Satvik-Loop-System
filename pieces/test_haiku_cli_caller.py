"""The Haiku 5.5 coder is called through the Claude CLI (owner exception 2026-10-31): no tools, no MCP servers from the user config,
no session files, and a reply counts only with status 200. These tests check the command that is built and the reply that is returned;
no model is called."""
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts" / "source"))
import fix_loop as fl  # noqa: E402


class Done:
    def __init__(self, returncode=0, stdout="FILE: pieces/x.py\n", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def run_caller(monkeypatch, done):
    seen = {}

    def fake_run(cmd, input=None, capture_output=None, text=None, encoding=None, timeout=None):
        seen.update(cmd=cmd, input=input, timeout=timeout)
        return done

    monkeypatch.setattr(subprocess, "run", fake_run)
    reply, info = fl._haiku_cli_caller_factory()("SYSTEM", "USER")
    return reply, info, seen


def test_command_uses_haiku_high_and_no_tools_or_mcp(monkeypatch):
    _, _, seen = run_caller(monkeypatch, Done())
    cmd = seen["cmd"]
    assert cmd[0] == "claude" and "-p" in cmd
    assert cmd[cmd.index("--model") + 1] == "claude-haiku-5-5"
    assert cmd[cmd.index("--effort") + 1] == "high"
    assert cmd[cmd.index("--tools") + 1] == ""
    assert "--strict-mcp-config" in cmd
    assert cmd[cmd.index("--mcp-config") + 1] == '{"mcpServers":{}}'
    assert "--no-session-persistence" in cmd


def test_prompt_goes_on_stdin_with_system_and_user_text(monkeypatch):
    _, _, seen = run_caller(monkeypatch, Done())
    assert seen["input"] == "SYSTEM\n\nUSER"
    assert seen["timeout"] == fl.HAIKU_CLI_TIMEOUT_S


def test_successful_reply_is_returned_with_status_200(monkeypatch):
    reply, info, _ = run_caller(monkeypatch, Done(stdout="FILE: pieces/x.py\n```python\nx = 1\n```\n"))
    assert reply.startswith("FILE: pieces/x.py")
    assert info["status"] == 200
    assert info["model"] == "claude-haiku-5-5"


def test_failed_cli_call_raises_instead_of_returning_text(monkeypatch):
    with pytest.raises(RuntimeError, match="claude CLI failed"):
        run_caller(monkeypatch, Done(returncode=1, stderr="auth failed"))
