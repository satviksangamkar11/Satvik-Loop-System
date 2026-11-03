"""Opus repair (final roster, 2026-11-02): the one repair call the production ladder may make. It runs the Claude CLI on the owner's subscription:
never the Anthropic API key, never an OpenRouter key, from an empty folder, with no tools and an empty MCP config. Its reply must carry FILE
blocks. Nothing here is retried with another model.

The model and effort are the roster's repair role (config/roster.json, "via": "claude_code"); nothing here names a model (2026-11-03). The call
itself is claude_code.run, the one isolated runner every Claude Code role uses. A repair role that is a sentinel (off, fake) makes no call; a
repair role on OpenRouter is called by p1b_production through the adapter, not here.

OPUS_ENABLED is False (patch 6, 2026-11-02): the owner decides the production switch. While it is off, a call to the model it gates
(claude_code.SWITCHED) raises RepairFailed before anything starts, and the job escalates with UNAVAILABLE. The test suite keeps it off
(pieces/conftest.py). Tests use a fake claude (pieces/fake_claude.py) or an injected runner, never the real CLI."""
import openrouter_call as oc
import claude_code as cc

TIMEOUT_S = 900                     # patch 6 (2026-11-02): 15 minutes; the repair runs only when 15 minutes of the unit deadline are left
MAX_OUTPUT_TOKENS = cc.MAX_OUTPUT_TOKENS    # CLAUDE_CODE_MAX_OUTPUT_TOKENS for the call (32000)
REPAIR_SYSTEM = ("You repair the files of one job. Reply with FILE blocks only: one for each file you were given, complete, and nothing else. "
                 "Never write a reviewer verdict line.")
OPUS_ENABLED = False                # owner decides the production switch (patch 6: the Reply OK check ran on Haiku, not Opus)
CLAUDE_CMD = None                   # None: claude_code.CLAUDE_CMD, else the `claude` on PATH; the tests point it at the fake claude
UNAVAILABLE = "Opus repair unavailable"
USAGE_LIMIT = cc.USAGE_LIMIT
RepairFailed = cc.ClaudeFailed      # the call produced no answer: switched off, a non-zero exit, an error flag, a usage limit or a timeout
RepairBadReply = cc.ClaudeBadReply  # the call answered, but the answer cannot be used: not JSON, no result text, or no FILE blocks
isolated_env = cc.isolated_env
real_call_refused = cc.real_call_refused


def switched_on():
    """Whether the owner has switched the repair step on (the module flag, read at call time)."""
    return OPUS_ENABLED


def repair_model(role="repair"):
    """(model, effort) of the roster's repair role (active profile). RepairFailed when that role is not a Claude Code model: a sentinel
    (off, fake) or an OpenRouter role makes no call here."""
    try:
        spec = oc.roster_state()["ROLE_SPECS"][role]
    except oc.RosterRefused as exc:
        raise RepairFailed(f"the roster is refused: {exc}") from exc
    if spec["model"] in oc.SENTINELS:
        raise RepairFailed(f"the {oc.active_profile()} profile has no {role} step ({role}={spec['model']}): no call was made")
    if oc.via_of(spec) != "claude_code":
        raise RepairFailed(f"the {oc.active_profile()} profile's {role} role is the {oc.via_of(spec)} model {spec['model']!r}: no Claude call was made")
    return spec["model"], spec["effort"]


def run_claude(prompt, cwd, claude_cmd, timeout_s, env, system_prompt=REPAIR_SYSTEM, model=None, effort=None):
    """The real call (only when switched on): claude_code.run with the repair role's model and effort (read from the roster when not given),
    the repair-only system prompt (None leaves it out: the probe) and the output cap. Returns (exit code, stdout, stderr). Raises
    subprocess.TimeoutExpired past timeout_s."""
    if model is None:
        model, effort = repair_model()
    return cc.run(prompt, cwd, claude_cmd, timeout_s, env, model=model, effort=effort, system_prompt=system_prompt,
                  max_output_tokens=MAX_OUTPUT_TOKENS)


def ask(prompt, *, raw_sink=None, runner=None, timeout_s=TIMEOUT_S, enabled=None, role="repair"):
    """The reply text of one isolated call, checked: it must be the JSON envelope of a successful call whose result text carries FILE blocks.
    The model and effort are the roster's `role`. `raw_sink` receives the raw output before it is parsed (the production run saves it to the
    job's history). `runner` replaces run_claude in tests (its first five arguments). Raises RepairFailed or RepairBadReply; nothing else is tried."""
    model, effort = repair_model(role)
    if cc.switched_off(model, OPUS_ENABLED if enabled is None else enabled):
        raise RepairFailed("Opus repair is switched off: no call was made")

    def default(prompt, cwd, claude_cmd, timeout_s, env):
        return run_claude(prompt, cwd, claude_cmd, timeout_s, env, model=model, effort=effort)

    code, out, err = cc.in_empty_folder(prompt, runner or default, cc.command(CLAUDE_CMD), timeout_s)
    if raw_sink is not None:
        raw_sink(out)
    text = cc.result_text(code, out, err)
    if USAGE_LIMIT.search(text) and not oc.FILE_BLOCK.search(text):
        raise RepairFailed("usage limit reached")
    if not oc.FILE_BLOCK.search(text):
        raise RepairBadReply("the reply has no FILE blocks")
    return text
