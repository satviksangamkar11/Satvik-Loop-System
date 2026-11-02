"""P0 execution boundary (2026-11-02): run pytest on generated code inside a locked-down Docker container.

What the container gets: a throw-away COPY of the needed source folders (never _moss/, _loop/, secrets/, .git, key files), the trusted test and
configuration files bind-mounted READ-ONLY over that copy, a controller-owned pytest config, and an output folder for the JUnit XML.
What it never gets: the network, host environment variables (API keys), the Docker socket, root, Linux capabilities, a writable root filesystem.
A timeout kills the whole container (every child process with it).

If Docker is missing, the image is missing, or the confinement self-test fails, the caller must STOP: there is no unrestricted fallback."""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

DEFAULT_IMAGE = os.environ.get("SATVIK_SANDBOX_IMAGE", "satvik-pytest-sandbox:latest")

# controller-owned pytest configuration: replaces any ini in the repo (pytest is started with -c) and fixes the JUnit format the verdict reads.
# The sandbox adds `pythonpath = /work`: the repo root goes on sys.path AFTER interpreter start-up (python -I), so a planted sitecustomize.py is
# never imported at start-up. pytest resolves pythonpath relative to the ini file, hence the absolute path.
CONTROLLER_INI = "[pytest]\njunit_family = xunit1\naddopts =\n"

EXCLUDED_NAMES = {"_moss", "_loop", "secrets", ".git", "__pycache__", ".pytest_cache", ".hypothesis", "node_modules", ".venv", "venv"}
EXCLUDED_SUFFIXES = (".env", ".key", ".pem", ".p12", ".pfx")


@dataclass
class RunResult:
    returncode: int
    output: str
    junit: str | None
    timed_out: bool


def _ignore(directory, names):
    out = []
    for n in names:
        if n in EXCLUDED_NAMES or n.endswith(EXCLUDED_SUFFIXES) or n.startswith(".env") or n.startswith("id_rsa") or n.startswith("id_ed25519"):
            out.append(n)
    return out


class DockerSandbox:
    def __init__(self, image: str = DEFAULT_IMAGE, include=None, cpus: str = "1", memory: str = "1g", pids: int = 256, docker: str = "docker"):
        self.image = image
        self.include = list(include or [])
        self.cpus, self.memory, self.pids = cpus, memory, pids
        self.docker = docker
        self._selftest_ok = False

    # ------------------------------------------------------------ availability and confinement
    def _docker(self, args, timeout=60, **kw):
        return subprocess.run([self.docker] + args, capture_output=True, text=True, timeout=timeout, **kw)

    def selftest(self) -> list:
        """Problems that make the sandbox unusable (empty list = usable). Checks the daemon, the image and the confinement inside a real container."""
        if self._selftest_ok:
            return []
        if shutil.which(self.docker) is None:
            return [f"{self.docker} not found"]
        try:
            v = self._docker(["version", "--format", "{{.Server.Version}}"], timeout=30)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return [f"docker not reachable: {exc}"]
        if v.returncode != 0 or not v.stdout.strip():
            return ["docker daemon not reachable: " + (v.stderr or v.stdout).strip()[:300]]
        img = self._docker(["image", "inspect", self.image], timeout=30)
        if img.returncode != 0:
            return [f"sandbox image {self.image} not found (build tools/sandbox/Dockerfile)"]
        with tempfile.TemporaryDirectory(prefix="satvik-selftest-") as tmp:
            tmpd = Path(tmp)
            (tmpd / "work").mkdir()
            ro = tmpd / "work" / "locked.txt"
            ro.write_text("locked", encoding="utf-8")
            probe = tmpd / "work" / "probe.py"
            probe.write_text(_PROBE, encoding="utf-8")
            _open_perms(tmpd)
            res = self._run_container(tmpd, ["python3", "-I", "/work/probe.py"], readonly={"locked.txt": ro}, timeout_s=60)
        problems = [ln[len("FAIL "):] for ln in res.output.splitlines() if ln.startswith("FAIL ")]
        if res.timed_out or res.returncode != 0 or "PROBE DONE" not in res.output:
            problems.append(f"confinement probe did not complete (exit {res.returncode}): {res.output.strip()[-300:]}")
        if not problems:
            self._selftest_ok = True
        return problems

    # ------------------------------------------------------------ pytest
    def run(self, root: Path, tests, overlay=None, readonly=(), timeout_s: int = 600) -> RunResult:
        """Run pytest on `tests` against a copy of `root`. overlay: rel -> host file copied over the workspace (negative controls).
        readonly: rel paths bind-mounted read-only from the host (trusted tests, conftest, config)."""
        root = Path(root)
        with tempfile.TemporaryDirectory(prefix="satvik-sandbox-") as tmp:
            tmpd = Path(tmp)
            work = tmpd / "work"
            work.mkdir()
            for inc in self._includes(root, tests, readonly, overlay):
                parts = PurePosixPath(inc).parts
                if not parts or any(_ignore(None, [part]) for part in parts):     # the include itself is checked too, not only what is inside it
                    continue
                src = root / inc
                dst = work / inc
                if src.is_dir():
                    shutil.copytree(src, dst, ignore=_ignore, dirs_exist_ok=True)
                elif src.is_file() and not _ignore(src.parent, [src.name]):
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(src, dst)
            for rel, src in (overlay or {}).items():
                dst = work / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src, dst)
            ro_files = {}
            for rel in readonly:
                host = root / rel
                if host.is_file():
                    ro_files[rel] = host
                    (work / rel).parent.mkdir(parents=True, exist_ok=True)
                    (work / rel).touch()           # mount point
            (tmpd / "p0").mkdir()
            (tmpd / "p0" / "pytest.ini").write_text(CONTROLLER_INI + "pythonpath = /work\n", encoding="utf-8")
            (tmpd / "out").mkdir()
            _open_perms(tmpd)
            cmd = ["python3", "-I", "-m", "pytest", "-c", "/p0/pytest.ini", "--rootdir", "/work", "-p", "no:cacheprovider", "--runxfail",
                   "-q", "--tb=short", "-rfE", "--junitxml", "/out/junit.xml"] + [str(PurePosixPath(t)) for t in tests]
            res = self._run_container(tmpd, cmd, readonly=ro_files, timeout_s=timeout_s, extra_mounts=[(tmpd / "p0", "/p0", "ro"), (tmpd / "out", "/out", "rw")])
            junit = tmpd / "out" / "junit.xml"
            res.junit = junit.read_text(encoding="utf-8") if junit.is_file() else None
            return res

    def _includes(self, root, tests, readonly, overlay):
        if self.include:
            return list(self.include)
        tops = set()
        for rel in list(tests) + list(readonly) + list(overlay or {}):
            parts = PurePosixPath(str(rel).split("::")[0]).parts
            tops.add(parts[0] if len(parts) > 1 else parts[0])
        return sorted(t for t in tops if t not in EXCLUDED_NAMES)

    def _run_container(self, tmpd: Path, cmd, readonly=None, timeout_s=600, extra_mounts=()) -> RunResult:
        name = "satvik-p0-" + uuid.uuid4().hex[:12]
        args = ["run", "--rm", "--name", name,
                "--network", "none", "--user", "65534:65534", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                "--read-only", "--tmpfs", "/tmp:rw,nosuid,nodev,size=64m",
                "--pids-limit", str(self.pids), "--memory", self.memory, "--memory-swap", self.memory, "--cpus", self.cpus,
                "--ipc", "none", "--init",
                "-e", "HOME=/tmp", "-e", "PYTHONDONTWRITEBYTECODE=1", "-e", "PYTHONHASHSEED=0",
                "--mount", _bind(tmpd / "work", "/work", False), "-w", "/work"]
        for host, target, mode in extra_mounts:
            args += ["--mount", _bind(host, target, mode == "ro")]
        for rel, host in (readonly or {}).items():
            args += ["--mount", _bind(host, "/work/" + str(PurePosixPath(rel)), True)]
        args += [self.image] + list(cmd)
        try:
            proc = subprocess.run([self.docker] + args, capture_output=True, text=True, timeout=timeout_s, env=_docker_cli_env())
            return RunResult(proc.returncode, proc.stdout + proc.stderr, None, False)
        except subprocess.TimeoutExpired as exc:
            self._kill(name)
            out = (exc.stdout or "") if isinstance(exc.stdout, str) else (exc.stdout or b"").decode("utf-8", "replace")
            return RunResult(124, out + f"\nSANDBOX: pytest timed out after {timeout_s} s; container {name} killed", None, True)
        except OSError as exc:
            return RunResult(125, f"SANDBOX: docker could not start: {exc}", None, False)

    def _kill(self, name):
        for args in (["kill", name], ["rm", "-f", name]):
            try:
                self._docker(args, timeout=30)
            except (OSError, subprocess.TimeoutExpired):
                pass


def _docker_cli_env() -> dict:
    """The docker CLI needs PATH/HOME/DOCKER_* only. Nothing else from the loop's environment is handed to it (and `docker run` passes none of
    the CLI's environment into the container unless -e names it)."""
    keep = {"PATH", "HOME", "DOCKER_HOST", "DOCKER_CONFIG", "DOCKER_CONTEXT", "DOCKER_CERT_PATH", "DOCKER_TLS_VERIFY",
            "SYSTEMROOT", "SYSTEMDRIVE", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "PROGRAMDATA", "TEMP", "TMP", "WINDIR", "COMSPEC", "PATHEXT"}
    return {k: v for k, v in os.environ.items() if k in keep}


def _bind(host, target: str, readonly: bool) -> str:
    """--mount syntax (not -v src:dst): a Windows drive letter (C:\\...) cannot be misread as the separator."""
    spec = f"type=bind,source={Path(host).resolve()},target={target}"
    return spec + (",readonly" if readonly else "")


def _open_perms(tmpd: Path):
    """The container runs as nobody (65534): the disposable copy must be readable and the work folder writable by it."""
    for dirpath, dirnames, filenames in os.walk(tmpd):
        os.chmod(dirpath, 0o777)
        for f in filenames:
            os.chmod(os.path.join(dirpath, f), 0o666)


_PROBE = r'''
import os, socket
def fail(msg): print("FAIL " + msg)
if os.getuid() == 0: fail("runs as root")
try:
    s = socket.create_connection(("1.1.1.1", 53), timeout=3); s.close(); fail("network reachable")
except OSError:
    pass
try:
    socket.getaddrinfo("pypi.org", 443); fail("DNS resolves: network reachable")
except OSError:
    pass
try:
    open("/work/locked.txt", "a").write("x"); fail("read-only mount is writable")
except OSError:
    pass
try:
    open("/etc/satvik_probe", "w").write("x"); fail("root filesystem is writable")
except OSError:
    pass
if os.path.exists("/var/run/docker.sock") or os.path.exists("/run/docker.sock"): fail("docker socket visible")
for k in os.environ:
    if any(w in k.upper() for w in ("API_KEY", "APIKEY", "ACCESS_KEY", "TOKEN", "SECRET", "PASSWORD", "OPENROUTER", "ANTHROPIC", "CLAUDE", "NVIDIA", "GROQ")):
        fail("credential-like environment variable visible: " + k)
try:
    caps = [l for l in open("/proc/self/status") if l.startswith("CapEff")][0].split()[1]
    if int(caps, 16) != 0: fail("capabilities not dropped: " + caps)
except (OSError, IndexError, ValueError):
    pass
print("PROBE DONE")
'''
