"""Claude Code calls (2026-11-03): the one runner for every roster role whose "via" is "claude_code" (config/roster.json). Any role may be a
Claude Code model at one of its efforts: the writers and reviewers of the production ladder, the repair step (opus_repair) and the planner
(plan_job). Which model and which effort a role uses is read from the roster; this module holds only the names the claude CLI accepts.

Every call is isolated the same way: plain `claude -p` with JSON output, no tools, an empty MCP config with --strict-mcp-config, no session
saved, run from an empty temporary folder, the ANTHROPIC_* and OPENROUTER* variables removed from its environment, the prompt on stdin. It
uses the owner's subscription, never an API key, and never the OpenRouter ledger. Under pytest, or with SATVIK_NO_OPUS=1, no real claude
process starts (the fake claude, pieces/fake_claude.py, is the one exception). The owner's switch (opus_repair.OPUS_ENABLED, off by default)
gates the one model it names (SWITCHED).

Importing this module calls no model."""
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

MODELS = ("opus", "sonnet", "haiku", "fable")       # what the claude CLI's --model takes: a vocabulary, never a role's choice (that is the roster)
EFFORTS = ("low", "medium", "high", "xhigh", "max")  # what the claude CLI's --effort takes
TEST_ONLY = ("haiku", "high")       # owner 2026-11-03: the only Claude Code model and effort the test profile allows
SWITCHED = "opus"                   # the model the owner's switch (opus_repair.OPUS_ENABLED) gates
TIMEOUT_S = 900                     # a role with no timeout_s in its roster settings gets this
MAX_OUTPUT_TOKENS = 32000           # CLAUDE_CODE_MAX_OUTPUT_TOKENS for a ladder call (writer, reviewer, repair)
CLAUDE_CMD = None                   # None: the `claude` on PATH; the tests point it at the fake claude
USAGE_LIMIT = re.compile(r"usage limit|limit reached|rate limit", re.IGNORECASE)


class _CallError(Exception):
    """A call that did not give a usable answer. exit_code is the claude process's exit code when the process ran to an end (0 for a clean
    exit with an error flag or a bad reply), and None when no process did: switched off, a refused command, a timeout."""

    def __init__(self, message, exit_code=None):
        super().__init__(message)
        self.exit_code = exit_code


class ClaudeFailed(_CallError):
    """The call produced no answer: refused before it started, switched off, a non-zero exit, an error flag, a usage limit or a timeout.
    Never retried with another model."""


class ClaudeBadReply(_CallError):
    """The call answered, but the answer cannot be used: not JSON, no result text, or it fails the reply check of its kind."""


def check_role(where, spec):
    """The roster rules for a role with "via": "claude_code": a model and an effort the CLI accepts, no OpenRouter provider and no prices.
    Returns the problem as text, or None."""
    if spec["model"] not in MODELS:
        return f"{where}: a claude_code model must be one of {', '.join(MODELS)}"
    if spec.get("effort") not in EFFORTS:
        return f"{where}: a claude_code role needs an effort, one of {', '.join(EFFORTS)}"
    if spec["provider"] is not None or spec["pricing"] is not None:
        return f"{where}: a claude_code role has no OpenRouter provider and no prices (provider and pricing are null)"
    return None


def test_profile_problem(where, spec):
    """The test profile's rule (owner 2026-11-03): a Claude Code role there is TEST_ONLY and nothing else. Returns the problem, or None."""
    if (spec["model"], spec.get("effort")) != TEST_ONLY:
        return (f"{where}: the test profile allows a claude_code role only as {TEST_ONLY[0]} at effort {TEST_ONLY[1]}; "
                f"the roster names {spec['model']} at effort {spec.get('effort')}")
    return None


def switched_off(model, enabled=None):
    """True when `model` is the one the owner's switch gates and the switch is off. `enabled` replaces opus_repair.OPUS_ENABLED (tests)."""
    if model != SWITCHED:
        return False
    if enabled is None:
        import opus_repair
        enabled = opus_repair.OPUS_ENABLED
    return not enabled


def isolated_env(environ=None):
    """The environment of the call: everything except the ANTHROPIC_* variables (API keys, tokens, base URLs) and every OpenRouter variable."""
    env = dict(os.environ if environ is None else environ)
    for key in list(env):
        upper = key.upper()
        if upper.startswith("ANTHROPIC_") or upper.startswith("OPENROUTER"):
            del env[key]
    return env


def real_call_refused(claude_cmd):
    """Test guard (2026-11-02): under pytest, or with SATVIK_NO_OPUS=1, no real Claude command may start. The fake claude is the one exception."""
    under_test = "PYTEST_CURRENT_TEST" in os.environ or os.environ.get("SATVIK_NO_OPUS") == "1"
    return under_test and not any("fake_claude" in str(part) for part in claude_cmd)


def command(override=None):
    """The claude command: `override`, else CLAUDE_CMD, else the `claude` on PATH."""
    chosen = override or CLAUDE_CMD
    return list(chosen) if chosen else [shutil.which("claude") or "claude"]


def run(prompt, cwd, claude_cmd, timeout_s, env, *, model, effort, system_prompt=None, max_output_tokens=MAX_OUTPUT_TOKENS):
    """The real call: plain `claude -p --model <model> --effort <effort>` with JSON output, no tools, an empty MCP config, no session saved,
    the system prompt when one is given, the output cap in the environment when one is given, run from `cwd`, the prompt on stdin. Returns
    (exit code, stdout, stderr). Raises ClaudeFailed before any process starts when the guard refuses the command or the model or effort is
    not one the CLI takes, and subprocess.TimeoutExpired past timeout_s."""
    if real_call_refused(claude_cmd):
        raise ClaudeFailed("a real Claude command is refused under pytest or SATVIK_NO_OPUS=1: no process was started")
    if model not in MODELS or effort not in EFFORTS:
        raise ClaudeFailed(f"model {model!r} at effort {effort!r} is not a Claude Code model and effort: no process was started")
    with tempfile.TemporaryDirectory(prefix="claude_mcp_") as config_dir:
        config = Path(config_dir) / "mcp.json"
        config.write_text('{"mcpServers": {}}', encoding="utf-8")
        cmd = list(claude_cmd) + ["-p", "--model", model, "--effort", effort, "--output-format", "json", "--tools", "",
                                  "--mcp-config", str(config), "--strict-mcp-config", "--no-session-persistence"]
        if system_prompt is not None:
            cmd += ["--system-prompt", system_prompt]
        env = dict(env)
        if max_output_tokens:
            env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] = str(max_output_tokens)
        proc = subprocess.run(cmd, input=prompt, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=timeout_s, env=env)
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def in_empty_folder(prompt, runner, claude_cmd, timeout_s):
    """One runner call from a fresh empty folder with the isolated environment: (exit code, stdout, stderr). A timeout is ClaudeFailed."""
    with tempfile.TemporaryDirectory(prefix="claude_cwd_") as empty:
        try:
            return runner(prompt, empty, claude_cmd, timeout_s, isolated_env())
        except subprocess.TimeoutExpired as exc:
            raise ClaudeFailed(f"the call did not finish in {timeout_s} s") from exc


def result_text(code, out, err):
    """The result text of a call's JSON envelope, or why it counts as nothing: a non-zero exit or an error flag is ClaudeFailed; output
    that is not a JSON object, or has no result text, is ClaudeBadReply."""
    if code != 0:
        raise ClaudeFailed(f"exit code {code}: {err[-300:]}", exit_code=code)
    try:
        envelope = json.loads(out)
    except ValueError as exc:
        raise ClaudeBadReply("the output is not JSON", exit_code=code) from exc
    if not isinstance(envelope, dict):
        raise ClaudeBadReply("the output is not a JSON object", exit_code=code)
    text = envelope.get("result")
    if envelope.get("is_error"):
        raise ClaudeFailed(f"the call reported an error: {str(text)[:300]}", exit_code=code)
    if not isinstance(text, str):
        raise ClaudeBadReply("the output has no result text", exit_code=code)
    return text


def envelope_facts(out):
    """What a raw output says about itself, for the job's history: its result text and its token counts (None when it has none)."""
    try:
        envelope = json.loads(out) if out else None
    except ValueError:
        envelope = None
    if not isinstance(envelope, dict):
        return {"reply_text": None, "usage": {}}
    usage = envelope.get("usage") if isinstance(envelope.get("usage"), dict) else {}
    tokens = {"prompt_tokens": usage.get("input_tokens"), "completion_tokens": usage.get("output_tokens")}
    text = envelope.get("result")
    return {"reply_text": text if isinstance(text, str) else None,
            "usage": {k: v for k, v in tokens.items() if isinstance(v, int) and not isinstance(v, bool)}}


def checked(kind, model, text):
    """The reply text after the same check an OpenRouter reply passes (openrouter_call.check_reply): a FILE block for a writer or repair, a
    final `## Verdict: READY|FIX FIRST` line for a reviewer. A usage-limit message that fails the check is ClaudeFailed, not a bad reply."""
    import openrouter_call as oc
    data = {"model": model, "choices": [{"message": {"content": text}, "finish_reason": "stop"}]}
    try:
        return oc.check_reply(kind, model, data)
    except oc.ReplyRejected as exc:
        if USAGE_LIMIT.search(text):
            raise ClaudeFailed("usage limit reached") from exc
        raise ClaudeBadReply(str(exc)) from exc


def role_spec(role, profile=None):
    """A Claude Code role's roster entry (active profile by default). ClaudeFailed when the role is not called through Claude Code."""
    import openrouter_call as oc
    spec = oc.roster_state(profile)["ROLE_SPECS"][role]
    if oc.via_of(spec) != "claude_code":
        raise ClaudeFailed(f"{role} is not a claude_code role in the {profile or oc.active_profile()} profile (model {spec['model']!r})")
    return spec


def ask_role(role, system, user, kind, *, raw_sink=None, runner=None, claude_cmd=None, enabled=None, profile=None, spec=None):
    """The checked reply of one isolated call for a writer or reviewer role on Claude Code: the role's model and effort from the roster,
    `system` as the system prompt, `user` on stdin, the role's roster timeout_s (TIMEOUT_S when it has none). kind: writer, repair or
    reviewer (openrouter_call.check_reply). `raw_sink` receives the raw output before it is parsed. `runner` replaces run in tests, with
    run's first five arguments. Raises ClaudeFailed or ClaudeBadReply; nothing else is tried. `spec` is the stage spec from a job's frozen
    snapshot (stage_config.call_spec): its model, effort and settings are used instead of the roster read now."""
    if spec is None:
        spec = role_spec(role, profile)
    elif spec.get("via") != "claude_code":
        raise ClaudeFailed(f"{role} is not a claude_code stage in the job's snapshot (model {spec.get('model')!r})")
    model, effort = spec["model"], spec["effort"]
    if switched_off(model, enabled):
        raise ClaudeFailed(f"{role} is {model}, which the owner's switch keeps off: no call was made")
    timeout_s = (spec.get("settings") or {}).get("timeout_s") or TIMEOUT_S
    cmd = command(claude_cmd)

    def default(prompt, cwd, claude_cmd, timeout_s, env):
        return run(prompt, cwd, claude_cmd, timeout_s, env, model=model, effort=effort, system_prompt=system)

    code, out, err = in_empty_folder(user, runner or default, cmd, timeout_s)
    if raw_sink is not None:
        raw_sink(out)
    return checked(kind, model, result_text(code, out, err))
