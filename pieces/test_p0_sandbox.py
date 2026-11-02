"""P0 (2026-11-02): adversarial tests of the Docker execution boundary.

ENVIRONMENT-DEPENDENT: these need a running Docker daemon and the sandbox image (tools/sandbox/Dockerfile, tag satvik-pytest-sandbox:latest or
SATVIK_SANDBOX_IMAGE). Without them every test here is SKIPPED, never passed.

Method: each attack is written as a test that PASSES ONLY IF THE ATTACK WORKS. Run inside the sandbox, every attack test must FAIL."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts" / "source"))
import fix_loop as fl  # noqa: E402
import p0_gates as gates  # noqa: E402
import sandbox_runner as sr  # noqa: E402

NL = "\n"


def _docker_ready():
    sb = sr.DockerSandbox()
    try:
        problems = sb.selftest()
    except Exception as exc:                 # noqa: BLE001
        problems = [str(exc)]
    return problems


_PROBLEMS = _docker_ready()
pytestmark = pytest.mark.skipif(bool(_PROBLEMS), reason="Docker sandbox not available: " + "; ".join(_PROBLEMS))


def _repo(tmp_path, files):
    root = tmp_path / "repo"
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return root


def _outcomes(res):
    return {tid: o for _f, tid, o in gates._cases(res.junit)} if res.junit else {}


def test_the_confinement_selftest_passes():
    assert sr.DockerSandbox().selftest() == []


def test_network_is_unreachable(tmp_path):
    root = _repo(tmp_path, {"pieces/test_attack.py": "import socket" + NL + "def test_attack():" + NL
                            + "    socket.create_connection(('1.1.1.1', 53), timeout=3).close()" + NL})
    res = sr.DockerSandbox(include=["pieces"]).run(root, ["pieces/test_attack.py"], timeout_s=120)
    assert _outcomes(res) == {"test_attack": "failed"}, res.output


def test_host_credentials_are_not_visible(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-p0-canary")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-p0-canary")
    root = _repo(tmp_path, {"pieces/test_attack.py": "import os" + NL + "def test_attack():" + NL
                            + "    assert os.environ.get('OPENROUTER_API_KEY') or os.environ.get('ANTHROPIC_API_KEY')" + NL})
    res = sr.DockerSandbox(include=["pieces"]).run(root, ["pieces/test_attack.py"], timeout_s=120)
    assert _outcomes(res) == {"test_attack": "failed"}
    assert "canary" not in res.output


def test_protected_folders_and_key_files_are_never_copied_in(tmp_path):
    root = _repo(tmp_path, {
        "_moss/work.txt": "protected moss work", "secrets/groq_key_1.txt": "gsk_canary", ".env": "OPENROUTER_API_KEY=sk-canary",
        "pieces/.env": "X=1", "pieces/server.key": "-----BEGIN KEY-----", "_loop/status.json": "{}",
        "pieces/test_attack.py": "from pathlib import Path" + NL + "def test_attack():" + NL
        + "    found = [p for p in ('/work/_moss/work.txt', '/work/secrets/groq_key_1.txt', '/work/.env', '/work/pieces/.env',"
        + " '/work/pieces/server.key', '/work/_loop/status.json') if Path(p).exists()]" + NL
        + "    assert found, 'nothing protected was visible'" + NL})
    sb = sr.DockerSandbox(include=["pieces", "_moss", "secrets", "_loop", ".env"])     # even when asked to include them
    res = sb.run(root, ["pieces/test_attack.py"], timeout_s=120)
    assert _outcomes(res) == {"test_attack": "failed"}, res.output


def test_trusted_test_files_are_read_only_and_the_host_copy_is_unchanged(tmp_path):
    acc = "def test_locked():" + NL + "    assert 1 + 1 == 2" + NL
    root = _repo(tmp_path, {
        "tests/acceptance/test_locked.py": acc,
        "pieces/test_attack.py": "def test_attack():" + NL
        + "    open('/work/tests/acceptance/test_locked.py', 'w').write('def test_locked():\\n    assert True\\n')" + NL})
    res = sr.DockerSandbox(include=["pieces", "tests"]).run(root, ["pieces/test_attack.py"], readonly=["tests/acceptance/test_locked.py"], timeout_s=120)
    assert _outcomes(res) == {"test_attack": "failed"}, res.output
    assert (root / "tests/acceptance/test_locked.py").read_text(encoding="utf-8") == acc


def test_writes_in_the_workspace_never_reach_the_host_checkout(tmp_path):
    root = _repo(tmp_path, {"pieces/m.py": "X = 1" + NL, "pieces/test_attack.py": "def test_attack():" + NL
                            + "    open('/work/pieces/m.py', 'w').write('X = 666\\n')" + NL + "    open('/work/pieces/new.py', 'w').write('Y = 1\\n')" + NL})
    sr.DockerSandbox(include=["pieces"]).run(root, ["pieces/test_attack.py"], timeout_s=120)
    assert (root / "pieces/m.py").read_text(encoding="utf-8") == "X = 1" + NL
    assert not (root / "pieces/new.py").exists()


def test_the_root_filesystem_is_read_only_and_not_root(tmp_path):
    root = _repo(tmp_path, {"pieces/test_attack.py": "import os" + NL
                            + "def test_attack_write():" + NL + "    open('/usr/local/lib/python3.13/os.py', 'a').write('#')" + NL
                            + "def test_attack_root():" + NL + "    assert os.getuid() == 0" + NL
                            + "def test_attack_docker_socket():" + NL + "    assert os.path.exists('/var/run/docker.sock')" + NL})
    res = sr.DockerSandbox(include=["pieces"]).run(root, ["pieces/test_attack.py"], timeout_s=120)
    assert _outcomes(res) == {"test_attack_write": "failed", "test_attack_root": "failed", "test_attack_docker_socket": "failed"}, res.output


def test_a_planted_sitecustomize_or_conftest_config_is_not_honoured(tmp_path):
    root = _repo(tmp_path, {
        "pieces/sitecustomize.py": "import builtins" + NL + "builtins.P0_PLANTED = True" + NL,
        "sitecustomize.py": "import builtins" + NL + "builtins.P0_PLANTED = True" + NL,
        "pieces/pytest.ini": "[pytest]" + NL + "addopts = -p no:junitxml --deselect pieces/test_attack.py::test_attack" + NL,
        "pieces/test_attack.py": "import builtins" + NL + "def test_attack():" + NL + "    assert getattr(builtins, 'P0_PLANTED', False)" + NL})
    res = sr.DockerSandbox(include=["pieces", "sitecustomize.py"]).run(root, ["pieces/test_attack.py"], timeout_s=120)
    assert _outcomes(res) == {"test_attack": "failed"}, res.output


def test_a_hung_test_with_child_processes_is_killed_with_its_container(tmp_path):
    root = _repo(tmp_path, {"pieces/test_attack.py": "import subprocess, sys" + NL + "def test_attack():" + NL
                            + "    subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(3600)'])" + NL
                            + "    while True:" + NL + "        pass" + NL})
    res = sr.DockerSandbox(include=["pieces"]).run(root, ["pieces/test_attack.py"], timeout_s=15)
    assert res.timed_out
    left = subprocess.run(["docker", "ps", "-a", "-q", "--filter", "name=satvik-p0-"], capture_output=True, text=True).stdout.strip()
    assert left == "", "a sandbox container survived its timeout"
    assert gates.verify_junit(res.junit, {"pieces/test_attack.py": {"test_attack"}}, res.returncode, res.timed_out)


def test_a_fork_bomb_is_capped_by_the_process_limit(tmp_path):
    root = _repo(tmp_path, {"pieces/test_attack.py": "import subprocess, sys" + NL + "def test_attack():" + NL
                            + "    procs = []" + NL + "    for _ in range(1000):" + NL
                            + "        procs.append(subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']))" + NL
                            + "    assert len(procs) == 1000" + NL})
    res = sr.DockerSandbox(include=["pieces"], memory="2g").run(root, ["pieces/test_attack.py"], timeout_s=120)
    assert _outcomes(res).get("test_attack") in ("failed", "error") or res.timed_out, res.output[-2000:]


# ---------------------------------------------------------------- the full production job through the real sandbox

def test_production_job_in_docker_rejects_the_constant_return_and_accepts_the_real_fix(tmp_path):
    import test_p0_acceptance as acc
    root = acc.make_prod_root(tmp_path / "bad")
    bad, _ = acc.run_prod(root, acc.CONSTANT_CODE, acc.CONSTANT_TEST, sandbox=sr.DockerSandbox(include=["pieces", "tests"]))
    assert bad["state"] == "escalate"
    root = acc.make_prod_root(tmp_path / "good")
    good, _ = acc.run_prod(root, acc.GOOD_CODE, acc.GOOD_TEST, sandbox=sr.DockerSandbox(include=["pieces", "tests"]))
    assert good["state"] == "passed", good
    assert good["evidence"]["tested_hash"] == good["evidence"]["reviewed_hash"] == good["evidence"]["final_hash"]


def test_production_job_stops_when_docker_is_missing(tmp_path):
    import test_p0_acceptance as acc
    root = acc.make_prod_root(tmp_path)
    res, calls = acc.run_prod(root, acc.GOOD_CODE, acc.GOOD_TEST, sandbox=sr.DockerSandbox(docker="docker-binary-that-does-not-exist"))
    assert res["state"] == "sandbox-unavailable" and calls == []
