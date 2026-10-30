from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path
from typing import Callable, Optional, Tuple, List, Dict, Any

DEFAULT_MODEL = "gpt-6-luna"
DEFAULT_EFFORT = "high"

# Load build_prompt from review_run.py by path
_REVIEW_RUN_PATH = Path(__file__).resolve().parents[2] / "scripts" / "source" / "review_run.py"
_spec = importlib.util.spec_from_file_location("review_run", _REVIEW_RUN_PATH)
if _spec is None or _spec.loader is None:
    raise ImportError(f"Cannot load review_run from {_REVIEW_RUN_PATH}")
_review_run = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_review_run)
build_prompt = _review_run.build_prompt  # type: ignore[attr-defined]


def codex_command(model: str, effort: str, out_path: Path) -> List[str]:
    return [
        "codex",
        "exec",
        "--sandbox",
        "read-only",
        "--skip-git-repo-check",
        "-m",
        model,
        "-c",
        f"model_reasoning_effort={effort}",
        "-o",
        str(out_path),
        "-",
    ]


def parse_verdict(text: str) -> str:
    verdict = "NONE"
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("## verdict:"):
            value = stripped.split(":", 1)[1].strip().upper()
            if value in {"READY", "FIX FIRST", "NONE"}:
                verdict = value
    return verdict


def _default_runner(cmd: List[str], input_text: str, timeout_s: int) -> Tuple[int, str]:
    """stdout/stderr go to a temp FILE, not pipes (helper processes of codex keep pipes open: P1-D8), and codex.cmd is found on Windows."""
    import shutil
    import subprocess
    import tempfile
    cmd = [shutil.which("codex.cmd") or shutil.which("codex") or cmd[0]] + list(cmd[1:])
    try:
        with tempfile.TemporaryFile() as sink:
            result = subprocess.run(cmd, input=input_text.encode("utf-8"), stdout=sink, stderr=sink, timeout=timeout_s)
            sink.seek(0)
            return result.returncode, sink.read().decode("utf-8", "replace")
    except subprocess.TimeoutExpired:
        return 1, "timeout"
    except OSError as exc:
        return 1, f"OSError: {exc}"


def _filter_secret_files(files: List[Path], root: Path) -> List[Path]:
    """Remove files that live under a secret directory (e.g., .secrets, secrets)."""
    secret_dirs = {".secrets", "secrets"}
    filtered = []
    for f in files:
        try:
            rel = f.relative_to(root)
        except ValueError:
            # File not under root; keep it (build_prompt will handle or ignore)
            filtered.append(f)
            continue
        # Check parent directories (excluding the file name itself)
        if any(part in secret_dirs for part in rel.parts[:-1]):
            continue
        filtered.append(f)
    return filtered


def run_codex_review(
    prompt_file: Path,
    files: List[Path],
    out_path: Path,
    *,
    root: Optional[Path] = None,
    model: str = DEFAULT_MODEL,
    effort: str = DEFAULT_EFFORT,
    timeout_s: int = 900,
    runner: Optional[Callable[[List[str], str, int], Tuple[int, str]]] = None,
) -> Dict[str, Any]:
    if out_path.exists():
        raise FileExistsError(f"Output file already exists: {out_path}")

    if not prompt_file.is_file():
        return {
            "model": model,
            "effort": effort,
            "out": str(out_path),
            "status": "refused",
            "verdict": "NONE",
            "problems": [],
            "chars": 0,
        }

    prompt_text = prompt_file.read_text(encoding="utf-8")
    if root is None:
        root = Path(__file__).resolve().parents[2]

    # Filter out secret files before building the prompt
    files = _filter_secret_files(files, root)

    built_text, problems = build_prompt(prompt_text, files, root, max_total_bytes=400000)
    chars = len(built_text)

    cmd = codex_command(model, effort, out_path)
    run_fn = runner or _default_runner
    returncode, output = run_fn(cmd, built_text, timeout_s)

    # Read the report written by codex
    if out_path.is_file() and out_path.stat().st_size > 0:
        report_text = out_path.read_text(encoding="utf-8")
        verdict = parse_verdict(report_text)
        if problems or returncode != 0:          # cut-off or missing input, or a failed reviewer process, can never be READY (Opus F2)
            verdict = "NONE"
        return {
            "model": model,
            "effort": effort,
            "out": str(out_path),
            "status": "ok",
            "verdict": verdict,
            "problems": problems,
            "chars": chars,
        }
    else:
        reason = output.strip() if output.strip() else "empty or missing report"
        out_path.write_text(f"REVIEW FAILED: {reason}", encoding="utf-8")
        return {
            "model": model,
            "effort": effort,
            "out": str(out_path),
            "status": "failed",
            "verdict": "NONE",
            "problems": problems,
            "chars": chars,
        }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="codex-review")
    parser.add_argument("--prompt-file", type=Path, required=True)
    parser.add_argument("--file", type=Path, action="append", default=[])
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--model", type=str, default=DEFAULT_MODEL)
    parser.add_argument("--effort", type=str, default=DEFAULT_EFFORT)
    parser.add_argument("--timeout-s", type=int, default=1800)
    args = parser.parse_args(argv)

    try:
        result = run_codex_review(
            prompt_file=args.prompt_file,
            files=args.file,
            out_path=args.out,
            model=args.model,
            effort=args.effort,
            timeout_s=args.timeout_s,
        )
    except FileExistsError:
        print(f"codex-review: status=refused verdict=NONE out={args.out}")
        return 2

    status = result["status"]
    verdict = result["verdict"]
    out = result["out"]
    print(f"codex-review: status={status} verdict={verdict} out={out}")

    if status == "ok":
        return 0
    elif status == "failed":
        return 1
    else:  # refused
        return 2


if __name__ == "__main__":
    sys.exit(main())
