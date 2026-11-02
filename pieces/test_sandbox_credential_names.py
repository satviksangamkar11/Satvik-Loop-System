"""P1a (2026-11-02): the sandbox probe refuses any credential-like variable name, not only the old short list (PRIVATE_KEY and
SIGNING_KEY slipped through). GPG_KEY stays allowed: python:3.13-slim sets it. The host runs the same source string the container probe runs,
so these tests need no Docker."""
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts" / "source"))
import sandbox_runner as sr  # noqa: E402


def credential_like():
    namespace = {}
    exec(sr.CREDENTIAL_RULE, namespace)
    return namespace["credential_like"]


@pytest.mark.parametrize("name", [
    "PRIVATE_KEY", "gpg_private_key", "SIGNING_KEY", "SSH_PRIVATE_KEY_FILE", "AWS_SECRET_ACCESS_KEY", "OPENROUTER_API_KEY",
    "DEEPSEEK_API_KEY", "ANTHROPIC_API_KEY", "GITHUB_TOKEN", "DB_PASSWORD", "my_api_key", "GROQ_KEY_1", "NVIDIA_NIM_KEY",
])
def test_secret_names_are_refused(name):
    assert credential_like()(name)


@pytest.mark.parametrize("name", ["GPG_KEY", "gpg_key"])
def test_known_gpg_key_is_allowed(name):
    assert not credential_like()(name)


@pytest.mark.parametrize("name", ["PATH", "HOME", "LANG", "HOSTNAME", "PYTHON_VERSION", "PYTHON_SHA256", "PYTHONDONTWRITEBYTECODE", "PYTHONPATH"])
def test_ordinary_names_pass(name):
    assert not credential_like()(name)


def test_container_probe_runs_the_same_rule():
    assert sr._PROBE.startswith(sr.CREDENTIAL_RULE)
    assert "if credential_like(k):" in sr._PROBE
