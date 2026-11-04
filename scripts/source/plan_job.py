"""P1c (2026-11-02, Revision 3): the Opus planner. One plain `claude -p` call per job: never --bare (that ignores the subscription login), never
the API key, from an empty folder, with no tools and an empty MCP config, output as JSON. plan/INTENT.json is written before the call and the raw
answer goes straight to plan/raw_response.json. A restart reuses a saved answer and never calls again; an intent with no saved answer is
planner-uncertain, and nothing retries it. The answer is checked against the production rules before anything is written; the job file is
then read-only and its hash is recorded. Nothing here names a legacy model family (a static test checks it).

The planner's model is the roster's planner role (2026-11-03), nothing here names one: "via": "claude_code" runs claude_code.run with the
role's model and effort; an OpenRouter planner is one checked text call through the adapter, kept in the same envelope shape. The planner is
not gated by the owner's repair switch (opus_repair.OPUS_ENABLED), as before."""
import hashlib
import json
import os
import re
import secrets
import stat
import subprocess
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

import jsonschema

import claude_code as cc
import event_log
import fix_loop as fl
import p0_gates as gates
import openrouter_call as oc
import p1b_routing as p1b

PLAN_TIMEOUT_S = 1800
MAX_FILES = 30
PLAN_SYSTEM = "You are the planner for one production job. Reply with ONE JSON object and nothing else."   # an OpenRouter planner's system prompt
TAG = re.compile(r"^(?:[-*]\s+)?(KNOWN|ASSUMED):\s+\S")
NL = chr(10)
INSTRUCTIONS = (
    "You are the planner for one production job. Reply with ONE JSON object and nothing else, with these keys:" + NL
    + "- plan_md: the plan in Markdown." + NL
    + "- spec_md: the specification. Every line that states a requirement starts with `KNOWN:` (taken from the request or the files) or "
      "`ASSUMED:` (your own assumption). Headings may stay untagged." + NL
    + "- job: a production job object. Roles only: fixers are writer_a and writer_b; reviewers is the string by_lineage; visibility is public; "
      "budget is {free_requests: 40, review_usd: 0.05, paid_usd: 0}; deadline_min is 60 or 120; name is the job name; files lists the files the coder may edit "
      "(never a test file that is locked); tests and acceptance_tests are controller-owned test files; negative_controls show that each "
      "acceptance test can fail on wrong code (or give acceptance_baseline_fail for a bug fix)." + NL
    + "- acceptance: {path: file text} for exactly the job's acceptance_tests." + NL
    + "- controls: {path: file text} for the source of every negative control." + NL
    + "- expected_lines: {path: line count} for every job file: at most 800 lines per file and 1500 in total." + NL)


def file_sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def claude_runner(prompt, cwd, claude_cmd, timeout_s, model=None, effort=None):
    """The real call: claude_code.run (plain `claude -p`, JSON out, no tools, an empty MCP config, no session saved, the isolated
    environment) with the planner role's model and effort (read from the roster when not given), run from the empty folder `cwd`. Returns
    (exit code, output). An empty output is replaced by an error envelope that keeps the exit code and the first error text, so a restart
    knows the call failed."""
    if model is None:
        spec = cc.role_spec("planner")
        model, effort = spec["model"], spec["effort"]
    code, out, err = cc.run(prompt, cwd, list(claude_cmd or ["claude"]), timeout_s, cc.isolated_env(), model=model, effort=effort,
                            system_prompt=None, max_output_tokens=None)
    if out.strip():
        return code, out
    return code, json.dumps({"is_error": True, "exit_code": code, "stderr": (err or "")[-2000:]})


def openrouter_runner(client):
    """A runner for a planner role on OpenRouter: one checked text call through the adapter, returned in the claude envelope shape (result,
    is_error), so the saved answer is read the same way. An adapter error is an error envelope."""
    def run(prompt, cwd, claude_cmd, timeout_s):
        try:
            text, info = client.call("planner", PLAN_SYSTEM, prompt, kind="text", timeout_s=timeout_s)
        except oc.OpenRouterError as exc:
            return 1, json.dumps({"type": "result", "is_error": True, "via": "openrouter", "result": str(exc)})
        return 0, json.dumps({"type": "result", "is_error": False, "via": "openrouter", "model": info.get("model"), "result": text})
    return run


def plan(root, job_name, plan_dir, request, files, runner=None, claude_cmd=None, timeout_s=PLAN_TIMEOUT_S, client=None):
    """Plans one job. Returns {state, reason, job_sha256, assumed}. States: planned, planner-uncertain, planner-refused, planner-invalid,
    planner-failed. The only call is made after INTENT.json is written; a restart never makes a second call. `client` is the OpenRouter
    client for a planner role on OpenRouter (default: one with the keys from secrets/ or the environment)."""
    try:                                                                  # the active roster names the planner: fake under test (no call)
        spec = oc.roster_state()["ROLE_SPECS"]["planner"]
    except oc.RosterRefused as exc:
        return _outcome("planner-refused", str(exc))
    model, via, effort = spec["model"], oc.via_of(spec), spec.get("effort")
    if model in oc.SENTINELS:
        return _outcome("planner-refused", f"the {oc.active_profile()} profile has no planner (planner={model})")
    root, plan_dir = Path(root), Path(plan_dir)
    intent_path, raw_path = plan_dir / "INTENT.json", plan_dir / "raw_response.json"
    fingerprint = request_fingerprint(job_name, request, files, via, model, effort)
    if intent_path.is_file():
        return _existing_intent(intent_path, raw_path, fingerprint, root, job_name, plan_dir)
    if len(files) > MAX_FILES:
        return _outcome("planner-refused", f"at most {MAX_FILES} files may be sent to the planner; got {len(files)}")
    if runner is None and via == "claude_code" and cc.real_call_refused(list(claude_cmd or ["claude"])):   # the guard: no real call under pytest or SATVIK_NO_OPUS=1
        return _outcome("planner-refused", "a real Claude command is refused under pytest or SATVIK_NO_OPUS=1: no call was made")
    if runner is None and via == "claude_code":
        def runner(prompt, cwd, claude_cmd, timeout_s):
            return claude_runner(prompt, cwd, claude_cmd, timeout_s, model=model, effort=effort)
    elif runner is None:
        runner = openrouter_runner(client or oc.OpenRouter(oc.load_keys(), root / "_loop" / "openrouter"))
    prompt = _prompt(root, request, files)
    plan_dir.mkdir(parents=True, exist_ok=True)
    try:                                       # the lock: only one plan creates INTENT.json; the other one reads it and never calls
        _create_intent(intent_path, {"job": job_name, "via": via, "model": model, "effort": effort, "files": list(files),
                                     "request_sha256": hashlib.sha256(request.encode("utf-8")).hexdigest(), "fingerprint": fingerprint,
                                     "timeout_s": timeout_s, "written": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
    except FileExistsError:
        return _existing_intent(intent_path, raw_path, fingerprint, root, job_name, plan_dir)
    try:
        with tempfile.TemporaryDirectory(prefix="plan_cwd_") as empty:
            with _planner_attempt(plan_dir, model, via, effort) as attempt:
                _code, raw = runner(prompt, cwd=empty, claude_cmd=claude_cmd, timeout_s=timeout_s)
                attempt["raw"] = raw
    except subprocess.TimeoutExpired:
        return _outcome("planner-uncertain", f"the call did not finish in {timeout_s} s: the outcome is unknown, and it is never retried")
    raw_path.write_text(raw, encoding="utf-8")
    return _from_answer(raw, root, job_name, plan_dir)


def request_fingerprint(job_name, request, files, via, model, effort):
    """The identity of one planning request: the job name, the request text, the files in order and the planner that answers it. A plan
    name is bound to this fingerprint: a different request under the same name is never served the earlier plan (I51)."""
    body = json.dumps({"job": job_name, "request": request, "files": list(files), "via": via, "model": model, "effort": effort},
                      sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _create_intent(path, data):
    """INTENT.json created exclusively (os.open with O_EXCL) and synced before the call. Of two plans for one name, only the one that
    creates the file may call the planner; the other finds the file and reads it (_existing_intent)."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(data, indent=2))
        fh.flush()
        os.fsync(fh.fileno())


def _existing_intent(intent_path, raw_path, fingerprint, root, job_name, plan_dir):
    """An intent already holds this plan name. The same request is served from its saved answer (no call); a different request is refused
    (request-conflict); an intent with no saved answer, or one still being written, is uncertain and never retried."""
    try:
        intent = json.loads(intent_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _outcome("planner-uncertain", "the intent is unreadable or still being written: the outcome is unknown, and it is never retried")
    if not isinstance(intent, dict) or intent.get("fingerprint") != fingerprint:
        return _outcome("request-conflict", "this job name already holds a plan for a different request, files or planner: a new request under the "
                                            "same name is refused, and the earlier plan is not served")
    if raw_path.is_file():
        return _from_answer(raw_path.read_text(encoding="utf-8"), root, job_name, plan_dir)     # reused: no call
    return _outcome("planner-uncertain", "the intent was written but no answer was saved: the outcome is unknown, and it is never retried")


def intent_conflict(root, job_name, request, files):
    """None when the plan name has no intent yet, or its intent is for this same request; else why a new plan under the name is refused.
    The API asks this before it accepts a request (409 plan-request-conflict)."""
    intent_path = Path(root) / "_loop" / job_name / "plan" / "INTENT.json"
    if not intent_path.is_file():
        return None
    try:
        spec = oc.roster_state()["ROLE_SPECS"]["planner"]
    except oc.RosterRefused:
        return None                                       # the planner call refuses by itself
    fingerprint = request_fingerprint(job_name, request, files, oc.via_of(spec), spec["model"], spec.get("effort"))
    try:
        intent = json.loads(intent_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "the plan name's intent is unreadable: a different request under this name is refused"
    if isinstance(intent, dict) and intent.get("fingerprint") == fingerprint:
        return None
    return "this job name already holds a plan for a different request, files or planner"


@contextmanager
def _planner_attempt(plan_dir, model, via, effort):
    """The planner call as an attempt-start and an attempt-end event (stage planner) in the plan's job folder, <job>/events.jsonl. The end
    event is written also when the call raises (a timeout, an OS error): failed, with the exception as its error. An event that cannot be
    written never changes the plan (event_log.emit never raises). The yielded dict takes the call's raw answer under "raw"."""
    job_dir = plan_dir.parent
    attempt_id = f"planner-{secrets.token_hex(4)}"
    event_log.emit(job_dir, "attempt-start", attempt_id=attempt_id, stage="planner", role="planner", mode="plan", model=model, via=via,
                   effort=effort)
    attempt, started = {"raw": None}, time.monotonic()
    error = None
    try:
        yield attempt
    except BaseException as exc:
        error = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        if error is None:
            error = _envelope_error(attempt["raw"])
        event_log.emit(job_dir, "attempt-end", attempt_id=attempt_id, stage="planner", role="planner", mode="plan", status="failed" if error
                       else "ok", failed=error is not None, prompt_tokens=None, completion_tokens=None, reasoning_tokens=None,
                       finish_reason=None, seconds=round(time.monotonic() - started, 3), **({"error": error[:300]} if error else {}))


def _envelope_error(raw):
    """None when the call's answer is a claude envelope (or openrouter envelope) without is_error; else why it counts as failed."""
    if raw is None:
        return "no answer"
    try:
        envelope = json.loads(raw)
    except ValueError:
        return "the answer is not JSON"
    if not isinstance(envelope, dict):
        return "the answer is not a JSON object"
    if envelope.get("is_error"):
        return str(envelope.get("result") or envelope.get("stderr") or envelope.get("exit_code") or "is_error")
    return None


def _prompt(root, request, files):
    parts = [INSTRUCTIONS, "REQUEST:", request, "", "FILES (chosen by the controller):"]
    base = root.resolve()
    for rel in files:
        path = (root / rel).resolve()
        if base not in path.parents or not path.is_file():
            raise ValueError(f"planner file is not a file under the repo: {rel}")
        parts.append(f"### {rel}" + NL + path.read_text(encoding="utf-8"))
    return NL.join(parts)


def _from_answer(raw, root, job_name, plan_dir):
    try:
        envelope = json.loads(raw)
    except ValueError:
        return _outcome("planner-invalid", "the output is not JSON")
    if not isinstance(envelope, dict):
        return _outcome("planner-invalid", "the output is not a JSON object")
    reported = [key for key in ("mcp_servers", "mcpServers", "mcp", "plugins") if envelope.get(key)]
    if reported:
        return _outcome("planner-refused", f"the session reports MCP servers or plugins ({', '.join(reported)}); the planner runs with none")
    if envelope.get("is_error"):
        detail = envelope.get("result") or envelope.get("stderr") or envelope.get("exit_code")
        return _outcome("planner-failed", f"the call reported an error: {str(detail)[:300]}")
    text = envelope.get("result")
    if not isinstance(text, str):
        return _outcome("planner-invalid", "the answer has no result text")
    try:
        answer = json.loads(_strip_fences(text))
    except ValueError:
        return _outcome("planner-invalid", "the answer text is not JSON")
    if not isinstance(answer, dict):
        return _outcome("planner-invalid", "the answer is not a JSON object")
    problems = _problems(answer, job_name, root, plan_dir)
    if problems:
        return _outcome("planner-invalid", "; ".join(problems))
    return _write(answer, plan_dir)


def _strip_fences(text):
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        stripped = NL.join(lines)
    return stripped


def spec_problems(spec_md):
    out = []
    for number, line in enumerate(spec_md.splitlines(), 1):
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        if not TAG.match(text):
            out.append(f"spec line {number} is not tagged KNOWN or ASSUMED")
    return out


def _path_problem(rel, root, context):
    """None when `rel` is a repo-relative path the engine accepts (fix_loop._validate_path: no absolute, drive or `..` path, no secrets
    component, inside the repo); else why not. A backslash is refused too, so no Windows-style path reaches the plan folder."""
    if not isinstance(rel, str) or not rel:
        return f"{context}: a path must be a non-empty string"
    if "\\" in rel or rel.startswith("/") or re.match(r"^[A-Za-z]:", rel):
        return f"{context}: absolute, drive or backslash path not allowed: {rel!r}"
    try:
        fl._validate_path(rel, Path(root), context)
    except ValueError as exc:
        return str(exc)
    return None


def _control_sources(job):
    """Every source the job's negative controls reference (the planner's controls must be exactly these)."""
    return {src for c in (job.get("negative_controls") or []) if isinstance(c, dict)
            for src in (c.get("replace") or {}).values() if isinstance(src, str)}


def _path_problems(answer, job, root, plan_dir):
    """Every path the answer would write or the job would install, checked before any write: the acceptance files are exactly the job's
    acceptance_tests, the controls are exactly the negative controls' sources (no extra file), and each destination is a safe relative path
    whose resolved location is inside plan_dir/files."""
    problems = []
    base = (Path(plan_dir) / "files").resolve()
    for key, expected, label in (("acceptance", set(job.get("acceptance_tests") or []), "acceptance"),
                                 ("controls", _control_sources(job), "controls")):
        files = answer.get(key)
        if not isinstance(files, dict) or not all(isinstance(v, str) for v in files.values()):
            problems.append(f"{label} must map each file path to its text")
            continue
        for rel in files:
            problem = _path_problem(rel, root, f"{label} path")
            if problem:
                problems.append(problem)
                continue
            if not (base / rel).resolve().is_relative_to(base):
                problems.append(f"{label} path escapes the plan folder: {rel!r}")
        extra = sorted(str(p) for p in set(files) - expected)
        if extra:
            problems.append(f"{label} has files the job does not reference: {extra}")
        if label == "acceptance" and set(files) != expected:
            problems.append("acceptance files must be exactly the job's acceptance_tests")
        if label == "controls" and set(files) != expected:
            problems.append(f"controls must be exactly the negative control sources; missing {sorted(expected - set(files))}")
    for rel in [job.get("spec")] + list(job.get("files") or []) + list(job.get("tests") or []) + list(job.get("acceptance_tests") or []):
        problem = _path_problem(rel, root, "job path")
        if problem:
            problems.append(problem)
    for c in job.get("negative_controls") or []:
        for target, src in ((c.get("replace") or {}).items() if isinstance(c, dict) else []):
            for rel in (target, src):
                problem = _path_problem(rel, root, "negative control path")
                if problem:
                    problems.append(problem)
    return problems


def _problems(answer, job_name, root, plan_dir):
    missing = [key for key in ("plan_md", "spec_md", "job", "acceptance", "controls", "expected_lines") if key not in answer]
    if missing:
        return [f"missing {', '.join(missing)}"]
    job = answer["job"]
    if not isinstance(job, dict):
        return ["the job must be an object"]
    problems = []
    if job.get("name") != job_name:
        problems.append("the job name differs from the job being planned")
    problems += _path_problems(answer, job, root, plan_dir)          # before anything is written: a path that escapes is refused here
    try:
        p1b.check_production_job(job)
    except p1b.JobRefused as exc:
        problems.append(str(exc))
    problems += gates.production_job_problems(job)
    try:
        jsonschema.validate(instance=job, schema=fl.JOB_SCHEMA)
    except jsonschema.ValidationError as exc:
        problems.append("job schema: " + exc.message)
    problems += spec_problems(str(answer["spec_md"]))
    for control in job.get("negative_controls") or []:
        for source in (control.get("replace") or {}).values():
            if isinstance(answer["controls"], dict) and source not in answer["controls"]:
                problems.append(f"negative control source {source} is missing from controls")
    lines = answer["expected_lines"]
    if not isinstance(lines, dict) or any(not isinstance(v, int) for v in lines.values()):
        problems.append("expected_lines must map each file to a line count")
    else:
        absent = [f for f in job.get("files") or [] if f not in lines]
        if absent:
            problems.append(f"expected_lines has no count for {absent}")
        try:
            p1b.check_job_size(lines)
        except p1b.JobTooLarge as exc:
            problems.append(str(exc))
    return problems


def _write(answer, plan_dir):
    job_path = plan_dir / "job.json"
    if job_path.is_file():                              # a restart: the artifacts of this answer are already written and read-only
        return _summary(plan_dir)
    job = answer["job"]
    base = (plan_dir / "files").resolve()
    targets = {}
    for rel, text in {**answer["acceptance"], **answer["controls"]}.items():   # every destination is checked before the first write
        target = (plan_dir / "files" / rel).resolve()
        if not target.is_relative_to(base):
            raise ValueError(f"refusing to write outside the plan folder: {rel!r}")
        targets[target] = str(text)
    plan_dir.mkdir(parents=True, exist_ok=True)
    (plan_dir / "PLAN.md").write_text(str(answer["plan_md"]), encoding="utf-8")
    (plan_dir / "SPEC.md").write_text(str(answer["spec_md"]), encoding="utf-8")
    (plan_dir / "expected_lines.json").write_text(json.dumps(answer["expected_lines"], indent=2, sort_keys=True), encoding="utf-8")
    for target, text in targets.items():
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    job_path.write_text(json.dumps(job, indent=2, sort_keys=True) + NL, encoding="utf-8")   # job.json last: its presence means the plan is complete
    os.chmod(job_path, stat.S_IREAD)                    # the planned job is fixed from here on
    return _summary(plan_dir)


def _summary(plan_dir):
    spec = (plan_dir / "SPEC.md").read_text(encoding="utf-8")
    assumed = [line.split(":", 1)[1].strip() for line in spec.splitlines() if re.match(r"^\s*(?:[-*]\s+)?ASSUMED:", line)]
    return {"state": "planned", "reason": None, "job_sha256": file_sha256(plan_dir / "job.json"), "assumed": assumed}


def _outcome(state, reason):
    return {"state": state, "reason": reason, "job_sha256": None, "assumed": []}
