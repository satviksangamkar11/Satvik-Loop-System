"""Opus repair (final roster, 2026-11-02): the isolated Claude CLI call, checked. The test suite keeps the switch off (pieces/conftest.py).
Every test here uses the fake `claude` (pieces/fake_claude.py) or an injected runner; the real CLI is never started. Also the static rules for
the P1b modules: no legacy family name, and the word Opus only in opus_repair.py."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts" / "source"))
import opus_repair as opus  # noqa: E402
import openrouter_call as oc  # noqa: E402

FAKE = REPO / "pieces" / "fake_claude.py"
FILE_TEXT = "FILE: pieces/calc.py\n```python\nx = 1\n```\n"


@pytest.fixture
def fake(tmp_path, monkeypatch):
    """A fake claude: `reply(envelope)` sets what it prints; `log()` returns the runs it recorded."""
    log, output = tmp_path / "fake.log", tmp_path / "out.json"
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(log))
    monkeypatch.setenv("FAKE_CLAUDE_OUTPUT", str(output))
    monkeypatch.setattr(opus, "CLAUDE_CMD", [sys.executable, str(FAKE)])

    class Fake:
        def reply(self, envelope):
            output.write_text(envelope if isinstance(envelope, str) else json.dumps(envelope), encoding="utf-8")

        def log(self):
            return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()] if log.exists() else []

    return Fake()


def envelope(result, is_error=False):
    return {"type": "result", "is_error": is_error, "result": result}


# ---- 1. off by default: no call of any kind

def test_the_test_run_keeps_the_repair_switch_off_and_the_reason_is_named():
    assert opus.OPUS_ENABLED is False                  # pieces/conftest.py: no test starts the real CLI
    assert opus.UNAVAILABLE == "Opus repair unavailable"


def test_switched_off_makes_no_subprocess_and_no_runner_call(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("a process was started while Opus repair is off")

    monkeypatch.setattr(subprocess, "run", refuse)
    with pytest.raises(opus.RepairFailed, match="switched off"):
        opus.ask("prompt", runner=refuse)


# ---- 2. the isolated call: flags, stdin, empty folder, environment

def test_the_call_uses_the_isolated_flags_and_the_prompt_on_stdin(fake):
    fake.reply(envelope(FILE_TEXT))
    text = opus.ask("the prompt text", enabled=True)
    assert text == FILE_TEXT
    run = fake.log()[0]
    args = run["args"]
    assert args[:6] == ["-p", "--model", "opus", "--effort", "medium", "--output-format"]
    assert args[6:8] == ["json", "--tools"] and "--strict-mcp-config" in args and "--mcp-config" in args
    assert run["stdin"] == "the prompt text"


def test_the_call_runs_from_an_empty_folder(fake):
    fake.reply(envelope(FILE_TEXT))
    opus.ask("p", enabled=True)
    run = fake.log()[0]
    assert run["cwd_entries"] == []                  # nothing in the folder the call starts from


def test_no_api_key_and_no_openrouter_key_reach_the_environment(fake, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-value")
    monkeypatch.setenv("OPENROUTER_FREE_KEY", "sk-or-test-free")
    monkeypatch.setenv("OPENROUTER_PAID_KEY", "sk-or-test-paid")
    fake.reply(envelope(FILE_TEXT))
    opus.ask("p", enabled=True)
    run = fake.log()[0]
    assert run["api_key_in_env"] is False
    env = opus.isolated_env()
    assert "ANTHROPIC_API_KEY" not in env and not [k for k in env if k.upper().startswith("OPENROUTER")]


# ---- 3. failures: none is retried with another model; each ends the call

def test_a_usage_limit_message_is_a_failed_call(fake):
    fake.reply(envelope("Claude AI usage limit reached. Your limit resets at 5pm.", is_error=True))
    with pytest.raises(opus.RepairFailed):
        opus.ask("p", enabled=True)


def test_an_error_flag_with_no_file_blocks_is_a_failed_call(fake):
    fake.reply(envelope("something went wrong", is_error=True))
    with pytest.raises(opus.RepairFailed, match="error"):
        opus.ask("p", enabled=True)


def test_a_timeout_is_a_failed_call_and_is_not_retried(fake, monkeypatch):
    fake.reply("sleep")
    monkeypatch.setenv("FAKE_CLAUDE_OUTPUT", "sleep")
    calls = []
    real_run = subprocess.run

    def counting(*args, **kwargs):
        calls.append(1)
        return real_run(*args, **kwargs)

    monkeypatch.setattr(subprocess, "run", counting)
    with pytest.raises(opus.RepairFailed, match="did not finish"):
        opus.ask("p", enabled=True, timeout_s=2)
    assert len(calls) == 1


def test_a_non_zero_exit_is_a_failed_call(fake):
    def runner(prompt, cwd, claude_cmd, timeout_s, env):
        return 1, "", "boom"
    with pytest.raises(opus.RepairFailed, match="exit code 1"):
        opus.ask("p", enabled=True, runner=runner)


# ---- 3b. the process exit code rides on the error (None when no process ended)

def test_a_non_zero_exit_carries_its_exit_code(fake):
    def runner(prompt, cwd, claude_cmd, timeout_s, env):
        return 3, "", "boom"
    with pytest.raises(opus.RepairFailed) as caught:
        opus.ask("p", enabled=True, runner=runner)
    assert caught.value.exit_code == 3


def test_an_error_flag_or_a_bad_reply_on_a_clean_exit_carries_exit_code_zero(fake):
    fake.reply(envelope("something went wrong", is_error=True))
    with pytest.raises(opus.RepairFailed) as failed:
        opus.ask("p", enabled=True)
    assert failed.value.exit_code == 0
    fake.reply(envelope("I would rather not write files."))
    with pytest.raises(opus.RepairBadReply) as bad:
        opus.ask("p", enabled=True)
    assert bad.value.exit_code == 0


def test_a_timeout_and_a_switch_off_carry_no_exit_code(monkeypatch):
    def timed_out(prompt, cwd, claude_cmd, timeout_s, env):
        raise subprocess.TimeoutExpired(cmd="claude", timeout=timeout_s)
    with pytest.raises(opus.RepairFailed) as timeout:
        opus.ask("p", enabled=True, runner=timed_out)
    assert timeout.value.exit_code is None
    with pytest.raises(opus.RepairFailed) as off:
        opus.ask("p", enabled=False, runner=timed_out)
    assert off.value.exit_code is None


def test_a_reply_with_no_file_blocks_is_a_bad_reply(fake):
    fake.reply(envelope("I would rather not write files."))
    with pytest.raises(opus.RepairBadReply, match="no FILE blocks"):
        opus.ask("p", enabled=True)


def test_output_that_is_not_json_is_a_bad_reply(fake):
    fake.reply("not json at all")
    with pytest.raises(opus.RepairBadReply):
        opus.ask("p", enabled=True)


# ---- 4. the raw reply is handed over before it is parsed (the production run saves it to history)

def test_the_raw_reply_reaches_the_sink_even_when_the_reply_is_then_refused(fake):
    fake.reply(envelope("no files here"))
    seen = []
    with pytest.raises(opus.RepairBadReply):
        opus.ask("p", enabled=True, raw_sink=seen.append)
    assert len(seen) == 1 and "no files here" in seen[0]


# ---- 5. static rules for the P1b modules

P1B_MODULES = ("openrouter_call.py", "p1b_routing.py", "p1b_production.py")
LEGACY = ("haiku", "sonnet", "nemotron", "codex", "groq", "nvidia")


@pytest.mark.parametrize("name", P1B_MODULES)
def test_no_p1b_module_names_a_legacy_family(name):
    text = (REPO / "scripts" / "source" / name).read_text(encoding="utf-8").lower()
    for word in LEGACY:
        assert word not in text, f"{name} names {word}"


@pytest.mark.parametrize("name", P1B_MODULES)
def test_the_word_opus_appears_in_no_p1b_module_but_opus_repair(name):
    text = (REPO / "scripts" / "source" / name).read_text(encoding="utf-8").lower()
    text = text.replace("opus_repair", "")          # the module's own name (the one place the word may appear in a P1b module's import)
    assert "opus" not in text, f"{name} names opus; the Opus call lives only in opus_repair.py"


def test_opus_is_a_model_name_only_in_opus_repair():
    text = (REPO / "scripts" / "source" / "opus_repair.py").read_text(encoding="utf-8")
    assert '"opus"' in text


# ---- 9. the test guard: a real Claude command never starts under pytest (2026-11-02)

def test_a_real_opus_command_under_pytest_is_refused_before_any_process_starts(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("a process was started under pytest")

    monkeypatch.setattr(opus, "OPUS_ENABLED", True)           # the switch on, as in production
    monkeypatch.setattr(opus, "CLAUDE_CMD", ["claude"])        # the real command, not the fake
    monkeypatch.setattr(subprocess, "run", refuse)
    with pytest.raises(opus.RepairFailed, match="refused"):
        opus.ask("prompt")


def test_the_guard_lets_the_fake_claude_through_under_pytest():
    assert opus.real_call_refused(["claude"]) is True
    assert opus.real_call_refused([sys.executable, str(FAKE)]) is False
