"""Escalation inside the loop (owner decision P1-D26): when a job ends `escalate` (3 Nemotron rounds failed), Sonnet 5.5 at MEDIUM effort,
read-only and with NO tools, diagnoses it from the escalation report, the spec and the job. Output: _loop/<job>/SONNET_DIAGNOSIS.md.
The model call goes through the Claude CLI (`claude -p --model sonnet --effort medium --tools ""`) with output redirected to a FILE, never pipes (P1-D8).
Stdlib only. Keys are never read or written here."""
import json
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

MODEL = "sonnet"
EFFORT = "medium"
MAX_CHARS = 60000
INSTRUCTIONS = (
    "You are a READ-ONLY diagnostician for a code-writing loop. A coder model (Nemotron) failed a job after 3 rounds. "
    "You have NO tools: everything you need is below. Decide for each failing test whether the cause is a SPEC DEFECT (ambiguous, contradictory or "
    "impossible requirement, wrong assumption about the data), a TEST DEFECT (the generated test asserts something the spec does not say) or a CODER DEFECT. "
    "Then give the minimal, concrete fixes (exact spec sentences to add or change). Do not write the module. Max 350 words. "
    "Start with one line: `CAUSE: SPEC` or `CAUSE: TEST` or `CAUSE: CODER` (the dominant one). Never print or ask for secrets or API keys."
)


def _read(path: Path, limit: int) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "(not available)"
    return text if len(text) <= limit else text[:limit] + "\n...(truncated)"


def build_prompt(job: dict, repo: Path) -> str:
    repo = Path(repo)
    name = job.get("name", "")
    status_dir = repo / "_loop" / name
    parts = [INSTRUCTIONS, "", "## JOB", json.dumps({k: job.get(k) for k in ("name", "spec", "files", "tests", "max_rounds", "reviewers")}, indent=1),
             "", "## ESCALATION REPORT (last pytest output)", _read(status_dir / "ESCALATE.md", 14000),
             "", "## SPEC", _read(repo / str(job.get("spec", "")), 24000),
             "", "## RULES", _read(repo / "docs" / "AGENT_RULES.md", 8000)]
    return "\n".join(parts)[:MAX_CHARS]


def default_runner(cmd, text, timeout_s):
    """File-based (no pipes). Resolves the claude executable. Returns (returncode, output text)."""
    cmd = [shutil.which("claude") or cmd[0]] + list(cmd[1:])
    try:
        with tempfile.TemporaryFile() as sink:
            result = subprocess.run(cmd, input=text.encode("utf-8"), stdout=sink, stderr=subprocess.DEVNULL, timeout=timeout_s)
            sink.seek(0)
            return result.returncode, sink.read().decode("utf-8", "replace")
    except subprocess.TimeoutExpired:
        return 1, "timeout"
    except OSError as exc:
        return 1, f"OSError: {exc}"


def diagnose(job_path, repo, runner=None, timeout_s: int = 900):
    """Writes _loop/<job>/SONNET_DIAGNOSIS.md and returns its path; returns None when the job left no ESCALATE.md. Never raises."""
    repo = Path(repo)
    try:
        job = json.loads(Path(job_path).read_text(encoding="utf-8"))
        status_dir = repo / "_loop" / str(job.get("name", ""))
        if not (status_dir / "ESCALATE.md").is_file():
            return None
        prompt = build_prompt(job, repo)
        cmd = ["claude", "-p", "--model", MODEL, "--effort", EFFORT, "--tools", ""]
        started = time.time()
        code, output = (runner or default_runner)(cmd, prompt, timeout_s)
        header = f"# Sonnet 5.5 ({EFFORT}) escalation diagnosis of job {job.get('name')} - OPINION, verify before acting\n\n"
        body = output.strip() if code == 0 and output.strip() else f"DIAGNOSIS UNAVAILABLE (exit {code}): {output.strip()[:300]}"
        out = status_dir / "SONNET_DIAGNOSIS.md"
        out.write_text(header + body + f"\n\n(wall {round(time.time() - started, 1)} s)\n", encoding="utf-8")
        return out
    except Exception as exc:                                   # an escalation helper must never break the launcher
        return None if not str(exc) else None
