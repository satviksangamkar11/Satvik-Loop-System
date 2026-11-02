"""Opus repair (final roster, 2026-11-02): the one repair call the production ladder may make. It runs the Claude CLI on the owner's subscription:
never the Anthropic API key, never an OpenRouter key, from an empty folder, with no tools and an empty MCP config. Its reply must carry FILE
blocks. Nothing here is retried with another model.

Off by default: OPUS_ENABLED is False, so no run starts a process until the owner switches it on (owner, 2026-11-02: no Opus call anywhere
without the owner's go). While it is off, ask() raises RepairFailed before anything starts, and the job escalates with UNAVAILABLE.
Tests use a fake claude (pieces/fake_claude.py) or an injected runner, never the real CLI."""
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import openrouter_call as oc

MODEL, EFFORT = "opus", "medium"
TIMEOUT_S = 1800
OPUS_ENABLED = False                # owner (2026-11-02): off until the owner switches it on
CLAUDE_CMD = None                   # None: the `claude` on PATH; the tests point it at the fake claude
UNAVAILABLE = "Opus repair unavailable"
USAGE_LIMIT = re.compile(r"usage limit|limit reached|rate limit", re.IGNORECASE)


def switched_on():
    """Whether the owner has switched the repair step on (the module flag, read at call time)."""
    return OPUS_ENABLED


class RepairFailed(Exception):
    """The call produced no answer: switched off, a non-zero exit, an error flag, a usage limit or a timeout. Never retried."""


class RepairBadReply(Exception):
    """The call answered, but the answer cannot be used: not JSON, no result text, or no FILE blocks."""


def isolated_env(environ=None):
    """The environment of the call: everything except the Anthropic API keys and every OpenRouter variable."""
    env = dict(os.environ if environ is None else environ)
    for key in list(env):
        upper = key.upper()
        if upper in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN") or upper.startswith("OPENROUTER"):
            del env[key]
    return env


def run_claude(prompt, cwd, claude_cmd, timeout_s, env):
    """The real call (only when switched on): plain `claude -p` with JSON output, no tools, an empty MCP config, run from `cwd`, the prompt on
    stdin. Returns (exit code, stdout, stderr). Raises subprocess.TimeoutExpired past timeout_s."""
    with tempfile.TemporaryDirectory(prefix="opus_mcp_") as config_dir:
        config = Path(config_dir) / "mcp.json"
        config.write_text('{"mcpServers": {}}', encoding="utf-8")
        cmd = list(claude_cmd) + ["-p", "--model", MODEL, "--effort", EFFORT, "--output-format", "json", "--tools", "",
                                  "--mcp-config", str(config), "--strict-mcp-config"]
        proc = subprocess.run(cmd, input=prompt, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=timeout_s, env=env)
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def ask(prompt, *, raw_sink=None, runner=None, timeout_s=TIMEOUT_S, enabled=None):
    """The reply text of one isolated call, checked: it must be the JSON envelope of a successful call whose result text carries FILE blocks.
    `raw_sink` receives the raw output before it is parsed (the production run saves it to the job's history). `runner` replaces run_claude
    in tests. Raises RepairFailed or RepairBadReply; nothing else is tried."""
    if not (OPUS_ENABLED if enabled is None else enabled):
        raise RepairFailed("Opus repair is switched off: no call was made")
    claude_cmd = list(CLAUDE_CMD) if CLAUDE_CMD else [shutil.which("claude") or "claude"]
    with tempfile.TemporaryDirectory(prefix="opus_cwd_") as empty:
        try:
            code, out, err = (runner or run_claude)(prompt, empty, claude_cmd, timeout_s, isolated_env())
        except subprocess.TimeoutExpired as exc:
            raise RepairFailed(f"the call did not finish in {timeout_s} s") from exc
    if raw_sink is not None:
        raw_sink(out)
    if code != 0:
        raise RepairFailed(f"exit code {code}: {err[-300:]}")
    try:
        envelope = json.loads(out)
    except ValueError as exc:
        raise RepairBadReply("the output is not JSON") from exc
    if not isinstance(envelope, dict):
        raise RepairBadReply("the output is not a JSON object")
    text = envelope.get("result")
    if envelope.get("is_error"):
        raise RepairFailed(f"the call reported an error: {str(text)[:300]}")
    if not isinstance(text, str):
        raise RepairBadReply("the output has no result text")
    if USAGE_LIMIT.search(text) and not oc.FILE_BLOCK.search(text):
        raise RepairFailed("usage limit reached")
    if not oc.FILE_BLOCK.search(text):
        raise RepairBadReply("the reply has no FILE blocks")
    return text
