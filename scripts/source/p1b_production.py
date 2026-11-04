"""P1b production runner (2026-11-02, Revision 3): runs one production unit through p1b_routing.walk(). Every model call goes through
openrouter_call by role (writer_a, writer_b, reviewer_a for GLM, reviewer_b for North). The repair step is a separate isolated call (opus_repair,
off by default), made at most twice per job and checked like any writer reply. The sandbox runs the job's tests and the locked acceptance tests, and those
decide. This module writes and restores the job files and records the evidence. It names no legacy model family (a static test checks it).

Any role may be a Claude Code model instead (2026-11-03, the roster's "via"): a writer or reviewer on Claude Code is one isolated call
(claude_code.ask_role) with the same reply check, the same history records (each call_NN.json says how it was called) and nothing reserved
on the OpenRouter ledger; a repair role on OpenRouter is called through the adapter like a writer's repair.

Pre-pilot review (2026-11-02): a fresh writer never receives a previous attempt's pytest output (the output goes to the job history instead);
every result that is not PASS puts the originals back and keeps the authorized candidate for inspection; the job is marked `running` before its
first write, so a killed worker is restored by the next launch (fix_loop._prepare_job_folder).

Review package (pre-pilot patch, Step 1): each reviewer gets ONE request with nine sections in this order: SPEC (requirement lines numbered),
DIFF, FILES, TESTS, RESULTS (from the JUnit file of the last green run), STATIC WARNINGS (advisory only), CONTEXT, OMITTED, CHECKS. Sections 1 to 6
and 9 are mandatory: if they do not fit PACKAGE_LIMIT the job is blocked before any reviewer call. Only CONTEXT is cut to fit. Excluded paths
(_loop, _moss, secrets, .git, .env, keys, and anything that resolves outside the repo) are never read."""
import ast
import copy
import difflib
import json
import os
import re
import secrets
import time
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from pathlib import Path

import inspect

import atomic_json
import claude_code as cc
import event_log
import openrouter_call as oc
import opus_repair
import p0_gates as gates
import p1b_routing as p1b
import redact
import sandbox_runner as sr
import stage_config as sc

def _envelope_models(raw):
    """The model identities a Claude Code output reports as served: the keys of its modelUsage and its model field, as strings. Empty when the
    output reports none (or is not a JSON object)."""
    try:
        envelope = json.loads(raw) if raw else None
    except (ValueError, TypeError):
        return []
    if not isinstance(envelope, dict):
        return []
    names = set()
    usage = envelope.get("modelUsage")
    if isinstance(usage, dict):
        names.update(k for k in usage if isinstance(k, str) and k)
    if isinstance(envelope.get("model"), str) and envelope["model"]:
        names.add(envelope["model"])
    return sorted(names)


def role_by_model():
    """The roster model -> role map for the active profile, read at call time."""
    return {p1b._m("writer_a"): "writer_a", p1b._m("writer_b"): "writer_b", p1b._m("reviewer_b"): "reviewer_b", p1b._m("reviewer_a"): "reviewer_a"}
PACKAGE_LIMIT = 100000              # characters: the whole review request (the owner's prompt limit for one reviewer call)
EXCLUDED_DIRS = set(gates.NEVER_INCLUDED) | set(sr.EXCLUDED_NAMES)
EXCLUDED_SUFFIXES = tuple(sr.EXCLUDED_SUFFIXES)
EXCLUDED_PREFIXES = (".env", "id_rsa", "id_ed25519")
CHECK_LINES = ("CHECK 1: every spec clause (SPEC n) has code and a test.",
               "CHECK 2: no code special-cases test values.",
               "CHECK 3: edge cases are handled.",
               "CHECK 4: nothing changed outside the spec.",
               "CHECK 5: no security or data-loss risk.")
VERDICT_LINE = ("Then the verdict line: `## Verdict: READY` or `## Verdict: FIX FIRST`. A FIX FIRST must name file:line of a file in this package "
                "and the SPEC n or CHECK n it breaks; without both it is not counted.")
TRIVIAL = (-1, 0, 1, 2)
CONTEXT_HEAD = "## 7. CONTEXT\n"
OMITTED_HEAD = "## 8. OMITTED (optional files left out for the size limit, and excluded references)\n"
WRITER_SYSTEM = ("You write the files of one job. Follow the spec exactly. Reply with FILE blocks only: for each file you were given, "
                 "`FILE: <path>` then the complete file in a fenced block, nothing else. Never write a reviewer verdict line.")
REVIEW_SYSTEM = ("REVIEW TASK. Review the files against the spec and the checks. Reply with at most 5 findings, each on ONE line, in exactly "
                 "this form:\n- file:line [SPEC n|CHECK n]: <what breaks, under 25 words>\n"
                 "Example: `- pieces/calc.py:12 [SPEC 3]: returns the sum without the tax, so the total is wrong.`\n"
                 "No code. No praise. If the code is right, give no finding line.\n"
                 "End your reply with exactly one line: `## Verdict: READY` or `## Verdict: FIX FIRST`. A FIX FIRST must have a finding line "
                 "that names file:line of a file in this package and the SPEC n or CHECK n it breaks; without both it is not counted.")
REPAIR_INPUT_CHARS = 6000           # the digest of the failing tests fed back to a repair: at most this many characters
STREAM_CHARS = 800                  # the captured stdout or stderr of one failing test in the digest: its last characters
FAILING_HEAD = "FAILING TESTS (from the JUnit file of the last run, failing tests only):\n"
FINDINGS_HEAD = "REVIEW FINDINGS (file:line | requirement it breaks | what breaks; the only problems to fix):\n"
EDIT_OPEN = "<<<<<<< SEARCH"
EDIT_BLOCK = re.compile(r"^<<<<<<< SEARCH[ \t]*\n(.*?)^=======[ \t]*\n(.*?)^>>>>>>> REPLACE[ \t]*$", re.S | re.M)
EDIT_SYSTEM = ("You repair the files of one job with SEARCH/REPLACE edits. Reply with edit blocks only. For each file you change, write "
               "`FILE: <path>` and then one or more blocks in exactly this form:\n"
               "<<<<<<< SEARCH\n<lines copied exactly from the file>\n=======\n<the replacement lines>\n>>>>>>> REPLACE\n"
               "Each SEARCH text must occur exactly once in its file. Change nothing else. Never write a reviewer verdict line.")
EDIT_EXAMPLE = ("FILE: pieces/calc.py\n"
                "<<<<<<< SEARCH\n"
                "    return sum(prices[:-1])\n"
                "=======\n"
                "    return sum(prices)\n"
                ">>>>>>> REPLACE")
WRITER_EXAMPLE = "FILE: pieces/calc.py\n```python\ntotal = sum(prices)\n```"     # 4 lines: the whole-file reply format
REASK = ("Your FIX FIRST had no valid file:line and SPEC n or CHECK n, so it is not counted. Reply again with that evidence, or with READY.")


def failing_summary(junit, limit=REPAIR_INPUT_CHARS, root=None):
    """The repair input: the failing tests of one JUnit run only. Each has its assertion line, its captured stdout and stderr (the last
    STREAM_CHARS characters of each) and, when `root` holds its test file, the source of its test function. Failures with the same digest are
    merged into one entry. Capped at `limit` characters."""
    if not junit:
        return "(no JUnit record of the last run)"
    try:
        cases = list(ET.fromstring(junit).iter("testcase"))
        details = gates._case_details(junit)
    except ET.ParseError:
        return "(the JUnit file could not be read)"
    groups = {}
    for case, (file, tid, _outcome, _kind) in zip(cases, details):
        bad = next((c for c in case if c.tag in ("failure", "error")), None)
        if bad is None:
            continue
        lines = [line.rstrip() for line in ((bad.text or "") + "\n" + (bad.get("message") or "")).splitlines() if line.strip()]
        source = [line.strip() for line in lines if line.lstrip().startswith((">", "E "))][:3] or [(bad.get("message") or "").strip()]
        where = [line.strip() for line in lines if re.match(r"\s*\S+\.py:\d+", line)][-1:]
        key = (tuple(source), tuple(where), _stream(case, "system-out"), _stream(case, "system-err"), _test_source(root, file, tid))
        groups.setdefault(key, []).append(f"{file}::{tid}" if file else tid)
    entries = []
    for (source, where, out_text, err_text, body), ids in groups.items():
        same = f" (identical failure, {len(ids)} tests)" if len(ids) > 1 else ""
        parts = [", ".join(ids) + same, *source, *where]
        if out_text:
            parts += ["stdout:", out_text]
        if err_text:
            parts += ["stderr:", err_text]
        if body:
            parts += ["test function:", body]
        entries.append("\n".join(parts))
    text = "\n\n".join(entries) or "(no failing test in the last run)"
    if len(text) > limit:
        cut = f"\n[cut at {limit} characters]"
        text = text[:limit - len(cut)] + cut
    return text


def _stream(case, tag):
    """The last STREAM_CHARS characters of a testcase's captured stdout or stderr ('' when it has none)."""
    node = case.find(tag)
    return ((node.text or "").strip() if node is not None else "")[-STREAM_CHARS:]


def _test_source(root, rel, tid):
    """The source of the test function a failing test id names, read from `root`; '' when there is no root, no such file or no such function."""
    path = readable(root, rel) if root is not None and rel else None
    if path is None:
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return ""
    name = gates._base(tid.split("::")[-1])
    lines = text.splitlines()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            start = min([node.lineno] + [d.lineno for d in node.decorator_list])
            return "\n".join(lines[start - 1:node.end_lineno])
    return ""


def _lines(text):
    """The lines of a text, without the empty piece after a final newline."""
    parts = text.split("\n")
    return parts[:-1] if parts[-1] == "" else parts


def _apply_one(content, search, replace):
    """(new text, None) for one SEARCH/REPLACE block, or (None, reason). An exact single match applies; else one match after each line's
    whitespace is normalized (runs collapsed, ends trimmed). Zero matches, or more than one, is refused."""
    if not search.strip():
        return None, "empty SEARCH text"
    hits = content.count(search)
    if hits == 1:
        return content.replace(search, replace, 1), None
    if hits > 1:
        return None, f"SEARCH text occurs {hits} times (ambiguous)"
    want = [" ".join(line.split()) for line in _lines(search)]
    have = content.split("\n")
    norm = [" ".join(line.split()) for line in have]
    spots = [i for i in range(len(have) - len(want) + 1) if norm[i:i + len(want)] == want]
    if not spots:
        return None, "SEARCH text not found"
    if len(spots) > 1:
        return None, f"SEARCH text matches {len(spots)} places after whitespace normalization (ambiguous)"
    i = spots[0]
    return "\n".join(have[:i] + _lines(replace) + have[i + len(want):]), None


def apply_edits(text, contents):
    """(changed, refusals) for the SEARCH/REPLACE blocks of a repair reply. `contents` maps each job file to its text on disk; `changed` maps each
    file the blocks change to its new text. Each block sits under a FILE: header. A block that is refused, malformed or unplaced, or names a file
    that is not in `contents`, refuses the whole reply: changed is {} and refusals names each refused block."""
    heads = list(oc.FILE_BLOCK.finditer(text))
    changed, refusals, placed = {}, [], 0
    for i, head in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
        blocks = [(m.group(1), m.group(2)) for m in EDIT_BLOCK.finditer(text[head.end():end])]
        placed += len(blocks)
        rel = head.group(1)
        if not blocks:
            continue
        if rel not in contents:
            refusals.append(f"{rel}: not a job file")
            continue
        current, bad = changed.get(rel, contents[rel]), False
        for search, replace in blocks:
            current, why = _apply_one(current, search, replace)
            if why is not None:
                refusals.append(f"{rel}: {why}")
                bad = True
                break
        if not bad:
            changed[rel] = current
    if text.count(EDIT_OPEN) != placed:
        refusals.append("a SEARCH/REPLACE block is malformed or has no FILE: header")
    if not placed and not refusals:
        refusals.append("no SEARCH/REPLACE block")
    return ({} if refusals else changed), refusals


class AcceptanceTampered(Exception):
    """A locked acceptance file changed while the job ran: nothing the job produced is trusted."""


def client_for(root, job, keys=None):
    """The job's OpenRouter client: the keys (secrets/ or the environment, or `keys` when given), the job's budget exactly as the job states it,
    the key snapshots under _loop/openrouter, and a job on the loop home (SATVIK_HOME, else <root>/_loop) for the shared ledger."""
    budget = job["budget"]
    client = oc.OpenRouter(oc.load_keys() if keys is None else keys, Path(root) / "_loop" / "openrouter",
                           budget=oc.JobBudget(free_requests=budget["free_requests"], review_usd=budget["review_usd"]))
    client.own_job(job.get("name") or "production-client", Path(root) / "_loop")     # launched or --solo: every call counts on the shared ledger (Track L); run() releases the lease
    return client


def read_text_exact(path) -> str:
    """The file's text exactly as its bytes are (utf-8, no newline translation). Text mode would turn \\r\\n into \\n on Windows and so
    drop bytes the hashes count (I69)."""
    return Path(path).read_bytes().decode("utf-8")


def write_text_exact(path, text) -> None:
    """Writes the text as exactly its utf-8 bytes. Text mode would write \\r\\n on Windows, so the disk would not hash to the text (I69)."""
    Path(path).write_bytes(text.encode("utf-8"))


def restore_files(root, originals):
    """Puts every job file back to its original text; a file that did not exist before the job is removed."""
    for rel, text in (originals or {}).items():
        path = Path(root) / rel
        if text is None:
            if path.exists():
                path.unlink()
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            write_text_exact(path, text)


GREEN_DIR = "green"                 # job folder/green/checkpoint.json: the last green run's files on disk, before any repair (I56)
GREEN_FILE = "checkpoint.json"


def _texts_hash(files_text) -> str:
    """The same digest as p0_gates.combined_hash, computed from the texts a checkpoint holds (utf-8, as they were written)."""
    import hashlib
    h = hashlib.sha256()
    for rel in sorted(files_text):
        text = files_text[rel]
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest() if text is not None else None
        h.update(rel.encode("utf-8") + b"\0" + (digest or "absent").encode("ascii") + b"\n")
    return h.hexdigest()


def write_green_checkpoint(folder, files_text: dict, tested_hash: str) -> Path:
    """Writes the green checkpoint atomically (temp file, then rename): the files as they passed the locked tests, with their hash. A reader
    sees all of it or none. Called before any repair step can change the files (I56)."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / GREEN_FILE
    tmp = folder / f".{GREEN_FILE}.{os.getpid()}.{time.time_ns()}.tmp"
    write_text_exact(tmp, json.dumps({"tested_hash": tested_hash, "files": files_text}, indent=2, sort_keys=True))   # exact bytes (I69)
    os.replace(tmp, target)
    return target


def read_green_checkpoint(folder):
    """The green checkpoint {tested_hash, files} from disk, or None when it is missing, unreadable or does not hash to its own tested_hash
    (fail closed: a damaged checkpoint is never restored)."""
    try:
        data = json.loads((Path(folder) / GREEN_FILE).read_text(encoding="utf-8"))
        files = data["files"]
        if not isinstance(files, dict) or _texts_hash(files) != data["tested_hash"]:
            return None
        return {"tested_hash": data["tested_hash"], "files": files}
    except (OSError, ValueError, KeyError, TypeError, UnicodeEncodeError):
        return None


def _snapshot(root, files, dest):
    """The candidate as it stood when the job stopped: the job's authorized files only, kept for inspection and never restored from."""
    for rel in files:
        src = Path(root) / rel
        if src.is_file():
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(src.read_bytes())


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _produced(root, files, originals):
    """True when a candidate exists: some job file on disk differs from its original. A stop before any write has no candidate."""
    for rel in files:
        path = Path(root) / rel
        now = read_text_exact(path) if path.is_file() else None      # exact bytes, as the originals were captured (I73): text mode turns CRLF into LF
        if now != (originals or {}).get(rel):
            return True
    return False


# ---------------------------------------------------------------- review package (Step 1)

def readable(root, rel):
    """The path of a repo file, or None when it resolves outside the repo, into an excluded folder, or is an excluded file (.env, keys)."""
    base = Path(root).resolve()
    try:
        path = (base / rel).resolve()
        parts = path.relative_to(base).parts
    except (OSError, ValueError):
        return None
    if not path.is_file() or any(part in EXCLUDED_DIRS for part in parts):
        return None
    if path.name.startswith(EXCLUDED_PREFIXES) or path.name.endswith(EXCLUDED_SUFFIXES):
        return None
    return path


def _read(root, rel):
    path = readable(root, rel)
    return None if path is None else path.read_text(encoding="utf-8", errors="replace")


def numbered(text):
    return "\n".join(f"{n:4d} | {line}" for n, line in enumerate(text.splitlines(), 1))


def spec_requirements(spec_text):
    """The requirement lines of the spec: every non-blank line that is not a markdown heading (KNOWN: and ASSUMED: lines included)."""
    return [line.strip() for line in spec_text.splitlines() if line.strip() and not line.strip().startswith("#")]


def diff_block(rel, before, now):
    if before is None and now is None:
        return f"{rel}: absent before and after (no change)"
    if before == now:
        return f"{rel}: (no change)"
    head = f"{rel}: NEW FILE" if before is None else f"{rel}: DELETED" if now is None else f"{rel}: changed"
    body = difflib.unified_diff((before or "").splitlines(), (now or "").splitlines(), fromfile=f"original/{rel}", tofile=f"current/{rel}",
                                lineterm="")
    return head + "\n" + "\n".join(body)


def results_section(junit, expected):
    """One line per expected test id: passed, failed, skipped or missing, from the JUnit XML of the last green run. `expected` is
    gates.expected_test_ids(...): rel -> set of ids, or None when the file cannot be read."""
    try:
        details = gates._case_details(junit) if junit else []
    except ET.ParseError:
        details = []
    seen = {}
    for file, tid, outcome, _kind in details:
        seen[(file, gates._base(tid))] = "failed" if outcome == "error" else outcome
    lines = []
    for rel in sorted(expected):
        ids = expected[rel]
        if ids is None:
            lines.append(f"{rel}: unreadable, so its tests cannot be listed")
            continue
        lines.extend(f"{rel}::{tid}: {seen.get((rel, tid), 'missing')}" for tid in sorted(ids))
    return "\n".join(lines) or "(no expected tests)"


def _parse(text):
    try:
        return ast.parse(text)
    except (SyntaxError, ValueError):
        return ast.parse("")


def _literal(node):
    if isinstance(node, ast.Constant) and not isinstance(node.value, bool):
        value = node.value
        if isinstance(value, str) and len(value.strip()) >= 3:
            return value
        if isinstance(value, (int, float)) and value not in TRIVIAL:
            return value
    return None


def _compared(tree):
    """(value, line) for each non-trivial literal that a comparison (==, in, ...) or a regex match is made against."""
    out = []
    for node in ast.walk(tree):
        sides = []
        if isinstance(node, ast.Compare):
            sides = [node.left, *node.comparators]
        elif isinstance(node, ast.Call) and node.args:
            name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
            if name in ("match", "search", "fullmatch"):
                sides = [node.args[0]]
        for side in sides:
            for item in (side.elts if isinstance(side, (ast.List, ast.Tuple, ast.Set)) else [side]):
                value = _literal(item)
                if value is not None:
                    out.append((value, node.lineno))
    return out


def static_warnings(test_texts, impl_texts):
    """Advisory only: a job's code compares against a literal that one of its tests compares against. Never decides a job on its own."""
    seen = {}
    for rel, text in test_texts.items():
        for value, line in _compared(_parse(text)):
            seen.setdefault(repr(value), f"{rel}:{line}")
    out = []
    for rel, text in impl_texts.items():
        for value, line in _compared(_parse(text)):
            key = repr(value)
            if key in seen:
                out.append(f"{rel}:{line} compares against {key}, which the test {seen[key]} also compares against")
    return out


def _module(rel):
    return rel[:-3].replace("/", ".") if rel.endswith(".py") else None


def _imported(text):
    """Module names a Python file imports, and module.name for each from-import name (so `from pieces import calc` names pieces.calc)."""
    out = set()
    for node in ast.walk(_parse(text)):
        if isinstance(node, ast.Import):
            out.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            out.add(node.module)
            out.update(f"{node.module}.{alias.name}" for alias in node.names)
    return out


def _repo_python_files(root):
    base = Path(root).resolve()
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = sorted(d for d in dirnames if d not in EXCLUDED_DIRS)
        for name in sorted(filenames):
            if name.endswith(".py"):
                yield Path(dirpath, name).relative_to(base).as_posix()


def _scrubbed(text, scrub_keys):
    """The one scrub for every artifact of a production run (I55): the loaded key values and the credential shapes (redact.scrub). scrub_keys
    is the run's set of exact values, as run() builds it."""
    return redact.scrub(text, scrub_keys)


def loaded_scrub_values(scrub_keys=()):
    """The exact values a production run scrubs from its evidence: the values in scrub_keys (a list of strings, or the {slot: key} dict that
    load_keys returns), the keys the adapter loads (secrets/ or the environment), and key-like environment variables. Never empty of what is
    loaded: a run that was given no keys still scrubs the ones it has (I55; the old list was empty for a production run)."""
    try:
        loaded = oc.load_keys()
    except Exception:                                    # no key file or variable: nothing loaded, the environment is still scrubbed
        loaded = {}
    return tuple(redact.values_of(scrub_keys or (), loaded, redact.env_key_values()))


def _scrubbed_obj(value, scrub_keys):
    """The same scrub for a whole JSON-shaped value (keys and strings, at every depth): status.json and the returned result use it."""
    if isinstance(value, str):
        return _scrubbed(value, scrub_keys)
    if isinstance(value, dict):
        return {_scrubbed(str(k), scrub_keys): _scrubbed_obj(v, scrub_keys) for k, v in value.items()}
    if isinstance(value, list):
        return [_scrubbed_obj(v, scrub_keys) for v in value]
    return value


def build_package(root, job, originals, junit, scrub_keys=(), limit=None):
    """(text, info): the nine-section review package for one review call. info = {"files": {path: lines} for every file shown,
    "spec_count": n, "kept": [context files], "omitted": [context files left out by name]}. Raises p1b.Blocked when the mandatory sections
    (1 to 6 and 9) do not fit in `limit` characters: no reviewer is called for it."""
    limit = PACKAGE_LIMIT if limit is None else limit
    root = Path(root)
    tests = list(job["tests"])
    locked = list(job.get("acceptance_tests") or []) + list(job.get("regression") or [])
    impl = [rel for rel in job["files"] if rel not in tests]
    job_files = set(job["files"]) | set(tests) | set(locked)
    shown = {}

    reqs = spec_requirements(_read(root, job["spec"]) or "")
    sections = {1: "## 1. SPEC\n" + ("\n".join(f"SPEC {n}: {line}" for n, line in enumerate(reqs, 1)) or "(no requirement lines)")}
    sections[2] = "## 2. DIFF\n" + "\n\n".join(diff_block(rel, originals.get(rel), _read(root, rel)) for rel in job["files"])

    blocks = []
    for rel in impl:
        now = _read(root, rel)
        if now is None:
            blocks.append(f"{rel}: (missing)")
            continue
        shown[rel] = len(now.splitlines())
        blocks.append(f"{rel}" + (" (unchanged)" if now == originals.get(rel) else "") + "\n" + numbered(now))
    sections[3] = "## 3. FILES\n" + ("\n\n".join(blocks) or "(none)")

    blocks = []
    for label, rels in (("[JOB TEST]", tests), ("[LOCKED ACCEPTANCE]", locked)):
        for rel in rels:
            now = _read(root, rel)
            if now is None:
                blocks.append(f"{rel} {label}: (missing)")
                continue
            shown[rel] = len(now.splitlines())
            blocks.append(f"{rel} {label}\n{numbered(now)}")
    sections[4] = "## 4. TESTS\n" + ("\n\n".join(blocks) or "(none)")

    targets = sorted(set(tests) | set(locked))
    sections[5] = "## 5. RESULTS (JUnit file of the last green run)\n" + results_section(junit, gates.expected_test_ids(root, targets))

    test_texts = {rel: _read(root, rel) for rel in targets}
    impl_texts = {rel: _read(root, rel) for rel in impl}
    warnings = static_warnings({rel: t for rel, t in test_texts.items() if t is not None},
                               {rel: t for rel, t in impl_texts.items() if t is not None})
    sections[6] = "## 6. STATIC WARNINGS (advisory: these never decide a job)\n" + ("\n".join(warnings) or "(none)")
    sections[9] = "## 9. CHECKS\n" + "\n".join(CHECK_LINES) + "\n" + VERDICT_LINE

    mandatory = [sections[k] for k in (1, 2, 3, 4, 5, 6, 9)]
    if len("\n\n".join(mandatory)) > limit:
        raise p1b.Blocked("review package exceeds the prompt limit")

    # CONTEXT: direct imports of the job's files and the job's reference files first, then callers (test callers included); smallest first
    # within each group. The rest is left out and named in OMITTED.
    imports, seen = {}, set(job_files)
    for rel in sorted(job_files):
        text = _read(root, rel)
        for mod in sorted(_imported(text) if text else set()):
            for cand in (mod.replace(".", "/") + ".py", mod.replace(".", "/") + "/__init__.py"):
                if cand not in seen and readable(root, cand) is not None:
                    imports[cand] = _read(root, cand)
                    seen.add(cand)
    references = {}
    excluded = []
    for rel in job.get("reference") or []:
        text = _read(root, rel)
        if text is None:
            excluded.append(rel)
        else:
            references[rel] = text
    job_modules = {_module(rel) for rel in job_files} - {None}
    callers = {}
    for rel in _repo_python_files(root):
        if rel in seen or rel in references:
            continue
        text = _read(root, rel)
        if text is not None and _imported(text) & job_modules:
            callers[rel] = text

    def ordered(group, role):
        return sorted(((rel, text, role) for rel, text in group.items()), key=lambda item: (len(item[1]), item[0]))

    candidates = ordered(imports, "import") + ordered(references, "reference") + ordered(callers, "caller")
    names = [rel for rel, _, _ in candidates] + excluded           # OMITTED can name at most these
    reserve = len(CONTEXT_HEAD) + len(OMITTED_HEAD) + len("\n".join(names) or "(none)") + len("\n\n".join(mandatory)) + 16
    budget = max(0, limit - reserve)
    kept, omitted, used = [], list(excluded), 0
    for rel, text, role in candidates:
        block = f"{rel} ({role})\n{numbered(text)}"
        if used + len(block) + 2 <= budget:
            kept.append((rel, block))
            used += len(block) + 2
            shown[rel] = len(text.splitlines())
        else:
            omitted.append(rel)
    sections[7] = CONTEXT_HEAD + ("\n\n".join(block for _, block in kept) or "(none)")
    sections[8] = OMITTED_HEAD + ("\n".join(omitted) or "(none)")

    text = "\n\n".join(sections[k] for k in range(1, 10))
    if len(text) > limit:                         # cannot happen while the reserve above holds; refuse rather than send an oversize request
        raise p1b.Blocked("review package exceeds the prompt limit")
    info = {"files": shown, "spec_count": len(reqs), "spec": reqs, "checks": list(CHECK_LINES), "kept": [rel for rel, _ in kept], "omitted": omitted}
    return _scrubbed(text, scrub_keys), info


def _accepts(fn, name):
    """True when `fn` takes the keyword `name` (or any keyword): a stand-in client or ask without it gets the call it had before."""
    try:
        params = inspect.signature(fn).parameters.values()
    except (TypeError, ValueError):
        return False
    return any(p.name == name or p.kind is inspect.Parameter.VAR_KEYWORD for p in params)


class Production:
    """The models object walk() drives: calls through openrouter_call, writes and restores the job files, runs the sandbox.

    stage_file is the job's frozen stage snapshot (stage_config), set by run(). With it every model call resolves through that file (marked
    started first, under the job's stage lock) and the ladder reads its role models from it (ladder_roles). Without it (None) every call reads
    the roster, as before."""

    stage_file = None
    _roles_cache = (None, None)

    def ladder_roles(self):
        """The roles view of the job's snapshot for p1b_routing (walk reads it), or None when the job has no snapshot. Read again when the
        file changes (apply_future_stage writes a new revision)."""
        if self.stage_file is None:
            return None
        try:
            stat = os.stat(self.stage_file)
            key = (stat.st_ino, stat.st_mtime_ns, stat.st_size)
        except OSError:
            key = None
        if key is None or self._roles_cache[0] != key:
            self._roles_cache = (key, sc.ladder_roles(sc.read_snapshot(self.stage_file.parent)))
        return self._roles_cache[1]

    def _role_of(self, model):
        view = self.ladder_roles()
        if view is None:
            return role_by_model()[model]
        return {view[role]["model"]: role for role in ("writer_a", "writer_b", "reviewer_b", "reviewer_a")}[model]

    def _stage_call(self, model, mode, role=None):
        """The call spec of this call from the job's snapshot, its stage marked started; None when the job has no snapshot."""
        if self.stage_file is None:
            return None
        return sc.begin_call(self.stage_file.parent, role or self._role_of(model), mode, fix=bool(self.fix_findings))

    def __init__(self, job, root, client, sandbox, *, protected, acceptance_lock, originals, parse_files, check_path, content_problem,
                 history=None, scrub_keys=(), repair_ask=None, job_dir=None, claude_ask=None, stop_check=None):
        self.job, self.root, self.client, self.sandbox = job, Path(root), client, sandbox
        self.stop_check = stop_check    # the operator's stop switch (fix_loop._stop_switch): raises StopRequested; checked before each new step and retry
        self.served = {}                # model (or the repair step's name) -> the model identities its last call reported (I46, see served_identities)
        self._repair_raw = None         # the raw output of the repair step's last call (its served identities are read from it)
        self.job_dir = Path(job_dir) if job_dir is not None else None      # the job folder: CONTRACT_CHECK.md is written here
        self.contract_file = None       # the path of CONTRACT_CHECK.md once it is written (status.json carries it)
        self.files, self.protected, self.acceptance_lock, self.originals = list(job["files"]), protected, acceptance_lock, originals
        self.parse_files, self.check_path, self.content_problem = parse_files, check_path, content_problem
        self.history, self.scrub_keys = history, tuple(scrub_keys)
        self.repair_ask, self.repair_calls = repair_ask, 0     # repair_ask(prompt, raw_sink) -> reply text; the default is opus_repair.ask
        self.claude_ask = claude_ask    # claude_ask(role, system, user, kind, raw_sink) -> checked reply text; the default is claude_code.ask_role
        self.test_targets = sorted(set(job["tests"]) | set(job["acceptance_tests"]) | set(job.get("regression") or []))
        self.spec = (self.root / job["spec"]).read_text(encoding="utf-8")
        self.review_text = ""           # the production review prompt is built in: a job's review_prompt is never read (the legacy docs/REVIEW_PROMPT.md is never used)
        self.steps = []                 # every model call and test run, in order
        self.last_junit = None          # the JUnit XML of the last test run (the repair input: failing tests only); cleared by restore()
        self.evidence_output = ""       # the output of the last test run, kept for the diagnostic
        self.test_runs = 0
        self.tested_hash = None         # hash of the job files at the last green run
        self.green_junit = None         # the JUnit XML of the last green run: the RESULTS section of the review package
        self.green_files = None         # the job files' texts at the last green run ({rel: text or None}): restore_green() puts them back
        self.reviewed_hashes = []       # hash of the job files at each review call (the whole job, kept as evidence)
        self.candidate_reviews = []     # hash of the job files at each review call of the candidate written last: the integrity check reads these
        self.fix_findings = None        # the stripped FIX FIRST findings the next repair call carries (stage_fix), None otherwise
        self.package = {"files": {}, "spec_count": 0}   # what the last review was given: read by p1b_routing.evidence_ok
        self.failed_calls = 0           # failed calls so far: each is history/failed_call_NN.json and a "failed" step in status.json
        self._seen_attempts = len(client.failed_attempts)   # the adapter's failed attempts before this job's calls belong to nobody here
        self._seen_requests = len(client.requests)          # the adapter's requests before this job's calls belong to nobody here
        self.calls_written = 0          # history/call_NN.json files so far: one per model call, failed or not
        self.waiting = None             # {why, since, model} while the job waits (model slot, account pace, 429 or retry backoff); None otherwise
        self._wait_open = None          # {why, model} of the wait whose wait-start event has no wait-end yet
        self._attempt_open = None       # the model call in flight: its attempt id, its call records and its last failure message
        self._attempt_tag, self._attempt_count = secrets.token_hex(3), 0
        if hasattr(client, "on_wait"):
            client.on_wait = self._set_waiting      # this object owns the field: only its own _heartbeat writes it, so no other write erases it

    # ---------------------------------------------------------------- model calls

    def stage_fix(self, findings):
        """The stripped FIX FIRST findings the next repair call carries to the writer, and nothing else of the review."""
        self.fix_findings = list(findings)

    def reject(self, model, mode, reason):
        """A writer reply routing cannot use (a file outside the job, or a fresh write missing job files): a failed call, HTTP status 200, the
        reason as its message."""
        self._failure(model, mode, 200, reason)

    def call(self, model, mode, files, slot=None):
        """One model call. `slot` (review modes only, optional): the reviewer slot the call fills, reviewer_b or reviewer_a. A backup writer
        filling a slot is recorded under the slot, not under its own role."""
        try:
            return self._call(model, mode, files, slot)
        finally:
            if mode in ("write", "repair"):
                self.fix_findings = None          # a staged finding goes to one repair call only

    def _call(self, model, mode, files, slot=None):
        if model == p1b.ANTHROPIC:
            return self._repair_step()
        if mode == "write":
            text = self._ask(model, WRITER_SYSTEM, self._writer_user("write"), kind="writer", mode="write")
            reply = self._usable(model, mode, text)
            self._step(model, mode, f"{len(reply)} file(s) returned")
            return reply
        if mode == "repair":
            return self._repair_writer(model)
        user = self._review_package(reask=(mode == "review-reask"))
        text = self._ask(model, REVIEW_SYSTEM + "\n\n" + self.review_text, user, kind="reviewer", mode=mode, slot=slot)
        self._check_locked()
        self.reviewed_hashes.append(gates.combined_hash(self.root, self.files))
        self.candidate_reviews.append(self.reviewed_hashes[-1])
        lines = text.strip().splitlines()
        self._step(model, mode, lines[-1] if lines else "(empty reply)")
        verdict = p1b.verdict_of(text)
        self._event("review", reviewer=model, mode=mode, verdict=verdict,
                    evidence_ok=p1b.evidence_ok(text, self.package) if verdict == "FIX FIRST" else None)
        if p1b.verdict_of(text) == "FIX FIRST" and not p1b.evidence_ok(text, self.package):
            self._failure(model, mode, 200, "FIX FIRST without valid evidence (file:line and SPEC n or CHECK n)")
        return text

    def _repair_writer(self, model):
        """A writer's repair: SEARCH/REPLACE edits first. A reply whose edits are refused is asked again once, with the refusal and the example.
        A second refusal, or a reply that holds no edits, goes to the whole-file request. Each refused reply is a failed call (reject), so its raw
        text stays in the job's history."""
        note = None
        for form in ("edit", "edit-reask"):
            text = self._ask(model, EDIT_SYSTEM, self._writer_user("repair", form=form, note=note), kind="repair", mode="repair")
            if EDIT_OPEN not in text:
                return self._accepted(model, self._usable(model, "repair", text))
            reply, note = self._apply_edits(text)
            if note is None:
                return self._accepted(model, reply)
            self.reject(model, "repair", f"edits refused: {note}")
        text = self._ask(model, WRITER_SYSTEM, self._writer_user("repair"), kind="repair", mode="repair")
        if EDIT_OPEN in text:
            self.reject(model, "repair", "whole-file repair reply holds SEARCH/REPLACE blocks")
            raise p1b.BadReply("whole-file repair reply holds SEARCH/REPLACE blocks")
        return self._accepted(model, self._usable(model, "repair", text))

    def _accepted(self, model, reply):
        self._step(model, "repair", f"{len(reply)} file(s) returned")
        return reply

    def _apply_edits(self, text):
        """(reply, None) when every SEARCH/REPLACE block applies to the job files as they are on disk and the result passes the checks a whole-file
        reply passes; else (None, why). Nothing is applied when any block is refused."""
        contents = {rel: (self.root / rel).read_text(encoding="utf-8") for rel in self.files if (self.root / rel).is_file()}
        changed, refusals = apply_edits(text, contents)
        if refusals:
            return None, "; ".join(refusals)
        try:
            return self._checked(changed, []), None
        except p1b.BadReply as exc:
            return None, str(exc)

    def served_identities(self, model):
        """The model identities that the last call for `model` (or the repair step, p1b.ANTHROPIC) actually ran with: the requested model, plus
        every model the call reported as served (I46). The ladder records them in the lineage and the panel excludes them all."""
        return list(self.served.get(model, [model]))

    def _check_stop(self):
        """Raises StopRequested when the stop switch is on. Called before each new model step (_ask, the repair step) and, through the client's
        wait hook, before each retry or wait (_set_waiting). A call in flight is never interrupted."""
        if self.stop_check is not None:
            self.stop_check()

    def _ask(self, model, system, user, kind, mode, role=None, slot=None):
        self._check_stop()
        spec = self._stage_call(model, mode, role)       # the job's snapshot (None: the roster, read now)
        role = role or self._role_of(model)
        via = spec["via"] if spec is not None else oc.roster_state()["VIA"][role]
        with self._attempt(model, role, mode, via, effort=(spec or {}).get("effort"), slot=slot):
            if via == "claude_code":
                return self._ask_claude(model, role, system, user, kind, mode, spec)
            try:
                if spec is None:
                    text, _info = self.client.call(role, system, user, kind=kind, effort=oc.effort_for_mode(mode))
                elif _accepts(self.client.call, "stage"):
                    text, _info = self.client.call(role, system, user, kind=kind, effort=spec["effort"], stage=spec)
                else:
                    text, _info = self.client.call(role, system, user, kind=kind, effort=spec["effort"])
                served = (_info or {}).get("model") if isinstance(_info, dict) else None     # the adapter checked it equals the requested model
                self.served[model] = sorted({model, served} - {None}) if isinstance(served, str) else [model]
            except oc.OpenRouterError as exc:
                self._record_history(model, mode)
                if isinstance(exc, oc.ReviewBudgetExceeded):     # the GLM review would pass the job's review ceiling: nothing was sent
                    raise p1b.BudgetStop(str(exc)) from exc
                if isinstance(exc, oc.QuotaExhausted):           # the day's free requests are used up: the job ends quota-exhausted, no further call
                    raise p1b.QuotaStop(str(exc)) from exc
                raise p1b.ModelFailed(str(exc)) from exc         # refused, dropped, quota, budget, account: this model is down for this job
            self._record_history(model, mode)
            return text

    def _ask_claude(self, model, role, system, user, kind, mode, spec=None):
        """A role on Claude Code: one isolated call (claude_code.ask_role) with the role's model and effort from the roster (or from the job's
        stage snapshot, `spec`), checked as an OpenRouter reply is (a FILE block for a writer or repair, a final Verdict line for a reviewer).
        It is one history/call_NN.json with via claude_code and no HTTP status, and nothing is reserved on the OpenRouter ledger. A failed or
        unusable call is a failed call and the model is down for this job, as an OpenRouter failure is."""
        raw = []
        ask = self.claude_ask or cc.ask_role
        started, text = time.monotonic(), None
        try:
            if spec is not None and _accepts(ask, "spec"):
                text = ask(role, system, user, kind, raw_sink=raw.append, spec=spec)
            else:
                text = ask(role, system, user, kind, raw_sink=raw.append)
            self.served[model] = sorted({model} | set(_envelope_models(raw[-1] if raw else None)))     # I46: the requested and the reported models
        except (cc.ClaudeFailed, cc.ClaudeBadReply) as exc:
            self._failure(model, mode, None, exc, facts={"exit_code": exc.exit_code})
            raise p1b.ModelFailed(str(exc)) from exc
        finally:
            facts = cc.envelope_facts(raw[-1] if raw else None)
            self._write_call({"model": spec["model"] if spec is not None else model, "via": "claude_code", "status": None, "failed": text is None,
                              "seconds": round(time.monotonic() - started, 3), "usage": facts["usage"],
                              "reply_text": facts["reply_text"] if facts["reply_text"] is not None else text}, mode)
        return text

    def _record_history(self, model, mode):
        """Every request the adapter sent since the last call here, failed or not: each is a history/call_NN.json. The failed ones are also failed calls."""
        fresh = self.client.requests[self._seen_requests:]
        self._seen_requests = len(self.client.requests)
        for request in fresh:
            self._write_call(request, mode)
        self._record_attempts(model, mode)

    def _write_call(self, request, mode):
        """One model call on the record: model, mode, the tokens its reply's usage reports (null when there is no reply), finish_reason, seconds.
        The reply's text and its reasoning text are kept too, scrubbed: a writer or repair reply in history/reply_NN.txt, a reviewer reply (review
        or review-reask, kept even when it has no verdict line) in history/review_NN.txt, and the reasoning in history/reasoning_NN.txt. The record
        links them as reply_file and reasoning_file (null when there is none). `via` says how the call was made: openrouter or claude_code."""
        self.calls_written += 1
        if self._attempt_open is not None:
            self._attempt_open["requests"].append(request)      # the attempt-end event sums its tokens
        if self.history is not None:
            n = self.calls_written
            usage = request.get("usage") or {}
            record = {"model": request.get("model"), "via": request.get("via") or "openrouter", "mode": mode, "status": request.get("status"),
                      "failed": bool(request.get("failed")),
                      "prompt_tokens": usage.get("prompt_tokens"), "completion_tokens": usage.get("completion_tokens"),
                      "reasoning_tokens": request.get("reasoning_tokens"), "finish_reason": request.get("finish_reason"), "seconds": request.get("seconds"),
                      "reply_file": None, "reasoning_file": None}
            reply = request.get("reply_text")
            if isinstance(reply, str):
                name = f"{'review' if mode in ('review', 'review-reask') else 'reply'}_{n:02d}.txt"
                (self.history / name).write_text(self._clean(reply), encoding="utf-8")
                record["reply_file"] = f"history/{name}"
            reasoning = request.get("reasoning_text")
            if isinstance(reasoning, str) and reasoning.strip():
                (self.history / f"reasoning_{n:02d}.txt").write_text(self._clean(reasoning), encoding="utf-8")
                record["reasoning_file"] = f"history/reasoning_{n:02d}.txt"
            (self.history / f"call_{n:02d}.json").write_text(json.dumps(record, indent=2), encoding="utf-8")

    def _clean(self, text):
        """A history text with every scrub key removed, then anything shaped like a key."""
        return oc.redact(_scrubbed(text, self.scrub_keys))

    def _record_attempts(self, model, mode):
        """Every failed attempt the adapter kept since the last call here, retries included: each one is a failed call."""
        fresh = self.client.failed_attempts[self._seen_attempts:]
        self._seen_attempts = len(self.client.failed_attempts)
        for attempt in fresh:
            self._failure(attempt["model"], mode, attempt["status"], attempt["message"], attempt.get("facts"))

    def _usable(self, model, mode, text, status=200):
        """The files of a reply. A reply the job cannot use is a failed call; its status is the HTTP status of that reply (200 for a writer)."""
        try:
            return self._parse(text)
        except p1b.BadReply as exc:
            self._failure(model, mode, status, exc)
            raise

    def _failure(self, model, mode, status, message, facts=None):
        """One failed call, kept whole: its model, its raw HTTP status (None when there is none) and the first 2,000 characters of its message with
        keys redacted. facts joins the history file: openrouter_call.reply_facts for a model call (finish_reason, usage and, for a reply with no
        message text, its message keys and reasoning lengths), or the process exit_code of the repair step's CLI call. It is a file in the job's
        history/ folder and a "failed" step in status.json; `call` names the mode it failed in."""
        self.failed_calls += 1
        text = oc.redact(_scrubbed(str(message), self.scrub_keys))[:oc.FAILURE_CHARS]
        step = {"model": model, "mode": "failed", "call": mode, "status": status, "message": text,
                "note": f"failed, HTTP {status}" if status is not None else "failed, no HTTP status"}
        if getattr(self, "_attempt_open", None) is not None:
            self._attempt_open["error"] = text                  # the attempt-end event of a failed call carries its last failure
        if self.history is not None:
            name = f"failed_call_{self.failed_calls:02d}.json"
            record = {"model": model, "call": mode, "status": status, "message": text, **_scrubbed_obj(facts or {}, self.scrub_keys)}
            (self.history / name).write_text(json.dumps(record, indent=2), encoding="utf-8")
            step["history"] = f"history/{name}"
        self.steps.append(step)
        self._heartbeat()

    def _repair_step(self):
        """The repair step: one isolated call (opus_repair). Its reply is scrubbed, saved raw to the job's history, and parsed like a writer's
        reply. Its failures end the step: an unavailable call carries the fixed escalation reason; nothing is retried with another model.
        A repair role on OpenRouter (the roster's "via") is one checked repair call through the adapter instead, recorded like a writer's."""
        self._check_stop()
        self.repair_calls += 1
        number = self.repair_calls
        stage = self._stage_call(p1b.ANTHROPIC, "repair", "repair")    # the job's snapshot (None: the roster, read now)
        spec = stage if stage is not None else oc.roster_state()["ROLE_SPECS"]["repair"]
        if self.repair_ask is None and oc.via_of(spec) == "openrouter" and spec["model"] not in oc.SENTINELS:
            return self._repair_openrouter(spec["model"])
        prompt = WRITER_SYSTEM + "\n\n" + self._writer_user("repair")

        def keep_raw(raw):
            self._repair_raw = raw                  # the served identities of this call are read from its output (I46)
            if self.history is not None:
                (self.history / f"repair_step_{number:02d}_raw.json").write_text(_scrubbed(raw, self.scrub_keys), encoding="utf-8")

        if stage is None:
            ask = self.repair_ask or (lambda text, raw_sink=None: opus_repair.ask(text, raw_sink=raw_sink))
        else:
            ask = self.repair_ask or (lambda text, raw_sink=None: opus_repair.ask(text, raw_sink=raw_sink, spec=stage))
        with self._attempt(p1b.ANTHROPIC, "repair", "repair", "claude_code", effort=(stage or {}).get("effort")):
            started, reply, text = time.monotonic(), None, None
            try:
                self._repair_raw = None
                text = ask(prompt, raw_sink=keep_raw)
                self.served[p1b.ANTHROPIC] = sorted({spec["model"]} | set(_envelope_models(self._repair_raw)))     # I46: requested + reported
                reply = self._usable(p1b.ANTHROPIC, "repair", _scrubbed(text, self.scrub_keys), None)
            except opus_repair.RepairFailed as exc:
                self._failure(p1b.ANTHROPIC, "repair", None, exc, facts={"exit_code": exc.exit_code})
                self._step(p1b.ANTHROPIC, "repair", f"unavailable: {_scrubbed(str(exc), self.scrub_keys)}")
                raise p1b.ModelFailed(str(exc), reason=opus_repair.UNAVAILABLE) from exc
            except opus_repair.RepairBadReply as exc:
                self._failure(p1b.ANTHROPIC, "repair", None, exc, facts={"exit_code": exc.exit_code})
                self._step(p1b.ANTHROPIC, "repair", f"bad reply: {exc}")
                raise p1b.BadReply(str(exc)) from exc
            finally:
                self._write_call({"model": p1b.ANTHROPIC, "via": "claude_code", "status": None, "failed": reply is None,
                                  "seconds": round(time.monotonic() - started, 3), "reply_text": text}, "repair")
        self._step(p1b.ANTHROPIC, "repair", f"{len(reply)} file(s) returned")
        return reply

    def _repair_openrouter(self, model):
        """The repair step on an OpenRouter model: the writer's whole-file repair request, one checked repair call through the adapter (its
        records are the adapter's requests), parsed like a writer's reply. An unavailable call carries the fixed escalation reason."""
        try:
            text = self._ask(model, WRITER_SYSTEM, self._writer_user("repair"), kind="repair", mode="repair", role="repair")
        except p1b.ModelFailed as exc:
            self._step(p1b.ANTHROPIC, "repair", f"unavailable: {_scrubbed(str(exc), self.scrub_keys)}")
            raise p1b.ModelFailed(str(exc), reason=opus_repair.UNAVAILABLE) from exc
        self.served[p1b.ANTHROPIC] = list(self.served_identities(model))     # I46: the repair step ran as this model
        reply = self._usable(p1b.ANTHROPIC, "repair", _scrubbed(text, self.scrub_keys), None)
        self._step(p1b.ANTHROPIC, "repair", f"{len(reply)} file(s) returned")
        return reply

    def _parse(self, text):
        ok, refused = self.parse_files(text)
        return self._checked(ok, refused)

    def _checked(self, ok, refused):
        """The files of a reply, or BadReply: a refused block for a job file, or a job file that fails the path or content checks."""
        refused_job = sorted({r[0] if isinstance(r, (list, tuple)) else r for r in refused} & set(self.files))
        if refused_job:
            raise p1b.BadReply(f"refused block for job file(s): {refused_job}")
        for rel, content in ok.items():
            if rel in self.files:
                problem = self.check_path(rel) or self.content_problem(rel, content)
                if problem:
                    raise p1b.BadReply(f"{rel}: {problem}")
        return ok

    def _writer_user(self, mode="write", form="whole", note=None):
        """The writer's request. A fresh write shows every job file. A repair shows only the files it needs: the files the FIX FIRST findings
        name, or, after a red test run, the failing test files and the code (the failing-test digest goes with them). Every job file is shown
        when no failing test file can be named. form: whole (complete files), edit (SEARCH/REPLACE blocks), or edit-reask (edit, after a refusal
        named by `note`). Every request ends with its format example: the 4-line whole-file example, or the SEARCH/REPLACE example for an edit form."""
        shown = self._repair_files() if mode == "repair" else list(self.files)
        parts = [f"SPEC:\n{self.spec}", "EXPECTED FILES:"]
        for rel in shown:
            path = self.root / rel
            parts.append(f"{rel}:\n{path.read_text(encoding='utf-8')}" if path.is_file() else f"{rel}: (new file)")
        if self.fix_findings:                       # a FIX FIRST repair: the findings only (never the review text, never a test run of another round)
            parts.append(FINDINGS_HEAD + "\n".join(self.fix_findings))
            parts.append("Fix exactly the findings above. Change nothing else.")
        elif self.last_junit is not None:
            parts.append(FAILING_HEAD + failing_summary(self.last_junit, root=self.root))
            parts.append("Fix exactly the problems shown above. Change nothing else.")
        if shown != list(self.files):
            parts.append("The job files not shown above are unchanged by this repair.")
        if form in ("edit", "edit-reask"):
            if form == "edit-reask":
                parts.append(f"Your edits were refused ({note}). Copy each SEARCH text exactly from the file as shown.")
            parts.append("Reply with SEARCH/REPLACE blocks for the files you change. Format example:\n" + EDIT_EXAMPLE)
        else:
            parts.append("Reply in exactly this format (an example, one file):\n" + WRITER_EXAMPLE)
        return "\n\n".join(parts)

    def _repair_files(self):
        """The job files a repair shows: the files the staged findings name; otherwise the failing test files and every code file."""
        if self.fix_findings:
            named = {p1b._path_of(note) for note in self.fix_findings}
            return [rel for rel in self.files if rel in named] or list(self.files)
        failing = self._failing_files()
        if not failing:
            return list(self.files)
        tests = set(self.job["tests"])
        return [rel for rel in self.files if rel not in tests or rel in failing]

    def _failing_files(self):
        """The job files that hold a failing test in the last run (from its JUnit file)."""
        if not self.last_junit:
            return set()
        try:
            details = gates._case_details(self.last_junit)
        except ET.ParseError:
            return set()
        return {file for file, _tid, outcome, _kind in details if outcome != "passed" and file in self.files}

    def _review_package(self, reask=False):
        """The one review request for this call: the nine-section package, with the re-ask note when this is the re-ask."""
        limit = PACKAGE_LIMIT - (len(REASK) + 2 if reask else 0)
        text, info = build_package(self.root, self.job, self.originals, self.green_junit, self.scrub_keys, limit=limit)
        self.package = info
        return text + ("\n\n" + REASK if reask else "")

    # ---------------------------------------------------------------- files and tests

    def write(self, reply):
        self.candidate_reviews = []             # a new candidate: only the reviews after this write belong to it
        for rel, text in reply.items():
            path = self.root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            write_text_exact(path, text)

    def restore(self):
        restore_files(self.root, self.originals)
        self.last_junit = None          # the next writer starts from the originals and the spec only: no test result of the attempt just undone

    def restore_green(self):
        """Puts the job files of the last green run back (a FIX FIRST repair that turned the tests red is undone): the candidate is again the
        code that passed every test and was reviewed, so the reviews of that code and its JUnit file belong to it again."""
        green_files, tested_hash = self.green_files, self.tested_hash
        if green_files is None and self.job_dir is not None:      # the memory copy is gone (a failed repair): the checkpoint on disk (I56)
            cp = read_green_checkpoint(self.job_dir / GREEN_DIR)
            if cp is not None:
                green_files, tested_hash = cp["files"], cp["tested_hash"]
        if green_files is None:
            raise AssertionError("restore_green before any green run")
        restore_files(self.root, green_files)
        self.last_junit = self.green_junit
        if gates.combined_hash(self.root, self.files) != tested_hash:
            raise AssertionError("the restored files are not the files of the last green run")
        self.tested_hash = tested_hash
        self.candidate_reviews = [h for h in self.reviewed_hashes if h == self.tested_hash]
        self._step("checkout", "restore-green", "the last green code is back on disk")
        self._event("restore-green")

    def tests(self, files):
        result = self.sandbox.run(self.root, self.test_targets, readonly=self.protected, timeout_s=600)
        self._check_locked()
        self.test_runs += 1
        self.evidence_output = result.output or ""
        if self.history is not None:                # the evidence of every run stays with the job, never in a writer's prompt
            (self.history / f"test_{self.test_runs:02d}.txt").write_text(_scrubbed(self.evidence_output, self.scrub_keys), encoding="utf-8")
        self.last_junit = result.junit
        expected = gates.expected_test_ids(self.root, self.test_targets)
        problems = gates.verify_junit(result.junit, expected, result.returncode, result.timed_out)
        failing = self._failing(result.junit, expected)
        green = not problems
        if green:
            self.tested_hash = gates.combined_hash(self.root, self.files)
            self.green_junit = result.junit
            self.green_files = {rel: read_text_exact(self.root / rel) if (self.root / rel).is_file() else None for rel in self.files}
            if self.job_dir is not None:                # the checkpoint goes to disk now, before any repair can change the files (I56)
                write_green_checkpoint(self.job_dir / GREEN_DIR, self.green_files, self.tested_hash)
        self._step("sandbox", "tests", "green" if green else f"red: {len(failing)} failing")
        self._event("tests", green=green, failing=sorted(failing))
        systemic = result.timed_out or result.junit is None or (not green and not failing)
        return p1b.Result(green, frozenset(failing), frozenset(t.split("::")[0] for t in failing), systemic)

    def _check_locked(self):
        if gates.hash_paths(self.root, self.protected) != self.acceptance_lock:
            raise AcceptanceTampered("locked acceptance files changed during the job")

    def _failing(self, xml, expected):
        """The failing test ids of this run as 'file::test', plus every expected test that was not collected at all."""
        try:
            details = gates._case_details(xml) if xml else []
        except ET.ParseError:
            details = []
        out = {f"{file}::{tid}" for file, tid, outcome, _kind in details if outcome != "passed"}
        for rel, ids in expected.items():
            if ids is None:
                out.add(rel)
                continue
            seen = {gates._base(tid) for file, tid, _outcome, _kind in details if file == rel}
            out |= {f"{rel}::{tid}" for tid in ids - seen}
        return out

    def contract_check(self, test_ids, dots_files, xing_files):
        """Dots and Xing both failed these locked acceptance tests with different code: the test id, its assertion message from the last run's
        JUnit, and the SPEC line it checks (the closest requirement line by shared words), written to CONTRACT_CHECK.md in the job folder."""
        if self.job_dir is None:
            return
        messages = _messages(self.last_junit, set(test_ids))
        blocks = []
        for tid in test_ids:
            message = messages.get(tid, "(no assertion message in the last run)")
            blocks.append(f"## {tid}\n- Assertion message: `{message}`\n- likely SPEC line (guessed by word overlap): {_spec_line(self.spec, tid, message)}\n")
        text = ("# CONTRACT CHECK\n\nDots and Xing both fail the same locked acceptance test, and their code differs. The spec and the locked "
                "acceptance test disagree or the spec is unclear. The ladder goes on; the repair step may still run.\n\n" + "\n".join(blocks))
        self.job_dir.mkdir(parents=True, exist_ok=True)
        (self.job_dir / "CONTRACT_CHECK.md").write_text(_scrubbed(text, self.scrub_keys), encoding="utf-8")
        self.contract_file = str(self.job_dir / "CONTRACT_CHECK.md")

    def _step(self, model, mode, note):
        self.steps.append({"model": model, "mode": mode, "note": note})
        self._heartbeat()

    def _set_waiting(self, waiting):
        """The client's wait hook: the wait that began ({why, since, model}) or None when it ended. Written at once, so status.json shows it."""
        if waiting:
            self._check_stop()                          # a retry or wait is a new request to the provider: not after a STOP (I38)
        self.waiting = dict(waiting) if waiting else None
        if self._wait_open is not None:                 # a wait that ends, or is replaced by a new one, gets its wait-end first
            self._event("wait-end", **self._wait_open)
            self._wait_open = None
        if waiting:
            self._wait_open = {"why": waiting.get("why"), "model": waiting.get("model")}
            self._event("wait-start", **self._wait_open)
        self._heartbeat()

    # ---------------------------------------------------------------- events (event_log): what the job did, one line each

    def _event(self, type_, **fields):
        """One line in the job's events.jsonl, scrubbed as status.json is. Never raises: an event never changes the job's outcome."""
        return event_log.emit(getattr(self, "job_dir", None), type_, **_scrubbed_obj(fields, getattr(self, "scrub_keys", ())))

    def _stage(self, role, mode, slot=None):
        """The stage type of a call, from its role and mode (best effort; None when no stage type fits). A review call names its panel slot
        (reviewer_b or reviewer_a), so a backup writer filling that slot is recorded under it. Without a slot, a review falls back to the role,
        then to the candidate's READY count: reviewer_a once it has one, reviewer_b before."""
        if mode == "review-reask":
            return "reviewer_reask"
        if mode == "review":
            if slot in ("reviewer_a", "reviewer_b"):
                return slot
            return role if role in ("reviewer_a", "reviewer_b") else ("reviewer_a" if self._ready_reviews() else "reviewer_b")
        if mode == "repair" and self.fix_findings:
            return "review_fix_repair"
        if role == "repair":
            return "specialist_repair"
        if mode == "write":
            return {"writer_a": "writer_a_write", "writer_b": "writer_b_write", "reviewer_b": "fallback_write"}.get(role)
        if mode == "repair":
            return {"writer_a": "writer_a_repair", "writer_b": "writer_b_repair"}.get(role)
        return None

    def _ready_reviews(self):
        """The READY reviews of the current candidate: review steps since the last write, repair or restore."""
        count = 0
        for step in reversed(self.steps):
            if step["mode"] in ("write", "repair", "restore-green"):
                break
            if step["mode"] == "review" and p1b.verdict_of(str(step.get("note", ""))) == "READY":
                count += 1
        return count

    @contextmanager
    def _attempt(self, model, role, mode, via, effort=None, slot=None):
        """One model call as an attempt-start event before it and an attempt-end event after it, also when it raises. The attempt-end sums
        the tokens of the call records written during it (retries included) and takes status and finish_reason from the last one."""
        self._attempt_count += 1
        rec = {"attempt_id": f"{self._attempt_tag}-{self._attempt_count:02d}", "requests": [], "error": None, "raised": False}
        try:
            if effort is None:                                   # no job snapshot: the roster's effort, read now
                effort = oc.effort_for_mode(mode) if via == "openrouter" else oc.roster_state()["ROLE_SPECS"][role].get("effort")
        except Exception:                       # the effort is a note on the event only; the call reads its own
            effort = None
        self._event("attempt-start", attempt_id=rec["attempt_id"], stage=self._stage(role, mode, slot), role=role, mode=mode, model=model, via=via,
                    effort=effort)
        outer, self._attempt_open = self._attempt_open, rec
        started = time.monotonic()
        try:
            yield rec
        except BaseException as exc:
            rec["raised"] = True
            rec["error"] = rec["error"] or f"{type(exc).__name__}: {exc}"
            raise
        finally:
            self._attempt_open = outer
            self._attempt_end(rec, time.monotonic() - started)

    def _attempt_end(self, rec, seconds):
        requests = rec["requests"]
        last = requests[-1] if requests else {}

        def total(get):
            values = [v for v in (get(r) for r in requests) if isinstance(v, (int, float)) and not isinstance(v, bool)]
            return sum(values) if values else None

        failed = rec["raised"] or bool(last.get("failed"))
        fields = {"attempt_id": rec["attempt_id"], "status": last.get("status"), "failed": failed,
                  "prompt_tokens": total(lambda r: (r.get("usage") or {}).get("prompt_tokens")),
                  "completion_tokens": total(lambda r: (r.get("usage") or {}).get("completion_tokens")),
                  "reasoning_tokens": total(lambda r: r.get("reasoning_tokens")), "finish_reason": last.get("finish_reason"),
                  "seconds": round(seconds, 3), "requests": len(requests)}
        if failed and rec["error"]:
            fields["error"] = self._clean(str(rec["error"]))[:oc.FAILURE_CHARS]
        self._event("attempt-end", **fields)

    def _heartbeat(self):
        """R1: status.json is rewritten after every step and failed call, so a running job shows its progress instead of a stale time. The
        fields are the end-of-job status's, with state running; the end-of-job write replaces this file."""
        if self.job_dir is None:
            return
        coder_calls, reviewer_calls = 0, {}
        for step in self.steps:
            if step["mode"] in ("write", "repair"):
                coder_calls += 1
            elif step["mode"] in ("review", "review-reask"):
                reviewer_calls[step["model"]] = reviewer_calls.get(step["model"], 0) + 1
        status = {"name": self.job["name"], "schema_version": 1, "state": "running", "profile": "production", "steps": self.steps,
                  "coder_calls": coder_calls, "reviewer_calls": reviewer_calls, "waiting": getattr(self, "waiting", None),
                  "updated": _now()}
        atomic_json.write_text_atomic(self.job_dir / "status.json", json.dumps(_scrubbed_obj(status, self.scrub_keys), indent=2))   # I58: never in place


def _messages(junit, ids):
    """{test id: assertion message} for the failing test ids in one JUnit run."""
    if not junit:
        return {}
    try:
        cases = list(ET.fromstring(junit).iter("testcase"))
        details = gates._case_details(junit)
    except ET.ParseError:
        return {}
    out = {}
    for case, (file, tid, _outcome, _kind) in zip(cases, details):
        bad = next((c for c in case if c.tag in ("failure", "error")), None)
        full_id = f"{file}::{tid}" if file else tid
        if bad is not None and full_id in ids:
            out[full_id] = bad.get("message") or bad.text or ""
    return out


def _spec_line(spec_text, test_id, message):
    """The SPEC line that shares the most words with the test's name and its assertion message; ties go to the first line."""
    def words(text):
        return set(re.findall(r"[a-z]{4,}", text.lower()))
    wanted = words(test_id.split("::")[-1].removeprefix("test_").replace("_", " ")) | words(message or "")
    best, score = "no SPEC line matches this assertion", 0
    for n, line in enumerate(spec_requirements(spec_text), 1):
        hit = len(wanted & words(line))
        if hit > score:
            best, score = f"SPEC {n}: {line}", hit
    return best


def run(job, root, status_dir_job, *, sandbox, protected, acceptance_lock, originals, evidence, client, parse_files, check_path,
        content_problem, scrub_keys=(), repair_ask=None, claude_ask=None, frozen_snapshot=None, stop_check=None):
    """Runs the production unit (_run), then releases the lease the job's client took for itself (client.release_job), whatever the outcome.
    frozen_snapshot: the stage table frozen when the job was queued (stage_config.snapshot, saved beside the queue file); None reads the roster
    now, as before."""
    scrub_keys = loaded_scrub_values(scrub_keys)
    try:
        return _run(job, root, status_dir_job, sandbox=sandbox, protected=protected, acceptance_lock=acceptance_lock, originals=originals,
                    evidence=evidence, client=client, parse_files=parse_files, check_path=check_path, content_problem=content_problem,
                    scrub_keys=scrub_keys, repair_ask=repair_ask, claude_ask=claude_ask, frozen_snapshot=frozen_snapshot,
                    stop_check=stop_check)
    finally:
        release = getattr(client, "release_job", None)
        if release is not None:
            release()


def _run(job, root, status_dir_job, *, sandbox, protected, acceptance_lock, originals, evidence, client, parse_files, check_path,
         content_problem, scrub_keys=(), repair_ask=None, claude_ask=None, frozen_snapshot=None, stop_check=None):
    """Runs the production unit. Returns the status dict (the same keys the legacy loop reports). Whatever the outcome, a job that did not
    PASS leaves the originals in the checkout, the authorized candidate under candidate/ (only when a candidate was written), and a status
    with its reason."""
    root, files = Path(root), list(job["files"])
    status_dir_job.mkdir(parents=True, exist_ok=True)
    history = status_dir_job / "history"
    history.mkdir(exist_ok=True)
    try:                                            # the job's stage table, frozen once: every model call of this job resolves through it
        if frozen_snapshot is not None:             # frozen at queue time (POST /jobs): used as it was saved, never re-read from the roster
            if frozen_snapshot.get("profile") != oc.active_profile():
                raise oc.RosterRefused(f"the frozen stage table is for profile {frozen_snapshot.get('profile')!r}, the run is {oc.active_profile()!r}",
                                       code="profile-mismatch")
            snap = copy.deepcopy(frozen_snapshot)
        else:
            snap, _sha = sc.snapshot(oc.active_profile())
    except oc.RosterRefused as exc:
        reason = f"stage configuration refused ({getattr(exc, 'code', 'roster-refused')}): {exc}"
        return _finish(job, root, status_dir_job, evidence, scrub_keys, None, p1b.Outcome("refused", reason), None, originals)
    stage_file = sc.write_snapshot(status_dir_job, snap)
    evidence["stage_snapshot"] = sc.snapshot_evidence(snap)
    try:
        client.begin_job()
    except oc.OpenRouterError as exc:
        kind = "quota-exhausted" if isinstance(exc, oc.QuotaExhausted) else "provider-unavailable"
        return _finish(job, root, status_dir_job, evidence, scrub_keys, None, p1b.Outcome(kind, f"key state: {exc}"), None, originals)
    atomic_json.write_text_atomic(status_dir_job / "status.json", json.dumps({"name": job["name"], "schema_version": 1, "state": "running", "updated": _now()}, indent=2))
    models = Production(job, root, client, sandbox, protected=protected, acceptance_lock=acceptance_lock, originals=originals,
                        parse_files=parse_files, check_path=check_path, content_problem=content_problem, history=history, scrub_keys=scrub_keys,
                       repair_ask=repair_ask, job_dir=status_dir_job, claude_ask=claude_ask, stop_check=stop_check)
    models.stage_file = stage_file
    lineage = p1b.Lineage()
    try:
        client.set_job_deadline(job["deadline_min"] * 60)     # the unit deadline: no retry (openrouter_call) starts after it
        outcome = p1b.walk(job, models, lineage)
        lineage.save(status_dir_job / "lineage.json")
        if outcome.status == "passed":
            mismatch = _integrity(files, root, models, protected, acceptance_lock, evidence)
            if mismatch:
                outcome = mismatch
    except AcceptanceTampered as exc:
        outcome = p1b.Outcome("acceptance-tampered", str(exc))
    except p1b.StopRequested as exc:                # the stop switch: no new step started; the files are restored by _finish, the state is "stopped"
        outcome = p1b.Outcome("stopped", f"stop switch is on: {exc}")
    except Exception as exc:                        # any other failure: the job does not pass, its files are restored (see _finish)
        outcome = p1b.Outcome("exception", f"{type(exc).__name__}: {exc}")
        if models is not None:
            try:                                    # the green candidate goes back before the snapshot below keeps the checkout (I56)
                models.restore_green()
            except Exception:                       # no green run, or no usable checkpoint: the files stay as the failure left them
                pass
    evidence["review_hashes"] = list(models.reviewed_hashes)
    evidence["budget"] = _budget_summary(client, models)
    evidence["openrouter_retries"] = list(client.retry_log)   # every retry of the job: model, attempt, wait, error (scrubbed in _finish)
    latest = sc.read_snapshot(status_dir_job)                 # the revision the job ended on (apply_future_stage may have added one)
    if latest is not None:
        evidence["stage_snapshot"] = sc.snapshot_evidence(latest)
    return _finish(job, root, status_dir_job, evidence, scrub_keys, models, outcome, models.evidence_output, originals)


def _budget_summary(client, models):
    """What the job spent of its budget: free requests used, review dollars spent, and the repair step's calls (attempts; a switched-off step
    starts no process)."""
    budget = client.budget
    return {"free_requests_used": budget.free_used, "free_requests": budget.free_requests,
            "review_usd_spent": round(budget.review_spent, 6), "review_usd": budget.review_usd,
            "repair_calls": models.repair_calls if models is not None else 0, "repair_enabled": opus_repair.switched_on()}


def _integrity(files, root, models, protected, acceptance_lock, evidence):
    """None when the delivered files are the files that passed the locked tests and were reviewed, and the locked acceptance files are unchanged."""
    final = gates.combined_hash(root, files)
    lock_ok = gates.hash_paths(root, protected) == acceptance_lock
    evidence.update(tested_hash=models.tested_hash, final_hash=final, reviewed_hash=models.reviewed_hashes[-1] if models.reviewed_hashes else None)
    same = bool(models.tested_hash) and final == models.tested_hash and bool(models.candidate_reviews) and all(h == final for h in models.candidate_reviews) and lock_ok
    if same:
        return None
    return p1b.Outcome("integrity-mismatch", "the files reviewed or delivered are not the files that passed the locked tests")


def _finish(job, root, status_dir_job, evidence, scrub_keys, models, outcome, last_output, originals):
    name = job["name"]
    if models is not None and models._wait_open is not None:     # a wait the client never closed still gets its wait-end
        models._event("wait-end", **models._wait_open)
        models._wait_open = None
    state, verdict, tests_passed = outcome.status, None, False
    if outcome.status == "passed":
        state, verdict, tests_passed = "passed", "READY", True
    elif outcome.status == "review-fix":
        state, verdict, tests_passed = "review-open", "FIX FIRST", True
        found = "".join(f"- {note}\n" for note in outcome.findings)
        (status_dir_job / "REVIEW_FIX.md").write_text(_scrubbed(f"# REVIEW FIX (the job ends review-open)\n\n{outcome.reason}\n"
                                                                + (f"\n## Findings\n\n{found}" if found else ""), scrub_keys), encoding="utf-8")
    elif outcome.status == "integrity-mismatch":
        state, verdict, tests_passed = "integrity-mismatch", "unavailable", True
    elif outcome.status in ("escalate", "blocked", "provider-unavailable", "budget-exceeded", "deadline", "acceptance-tampered", "exception"):
        state = outcome.status
    if state != "passed":                           # nothing that did not pass stays in the checkout: keep the candidate if one was produced, then restore
        if _produced(root, job["files"], originals):
            _snapshot(root, job["files"], status_dir_job / "candidate")
        restore_files(root, originals)
    if state not in ("passed", "review-open"):
        diagnostic = "EXCEPTION.md" if state == "exception" else "ESCALATE.md"
        (status_dir_job / diagnostic).write_text(_scrubbed(
            f"# Escalation Report\n\nJob: {name}\n\nState: {state}\n\nReason: {outcome.reason}\n\nLast test output (last 9000 chars):\n```\n"
            f"{(last_output or '')[-9000:]}\n```\n", scrub_keys), encoding="utf-8")
    steps = models.steps if models else []
    reviewer_calls, coder_calls = {}, 0
    for step in steps:
        if step["mode"] in ("write", "repair"):
            coder_calls += 1
        elif step["mode"] in ("review", "review-reask"):
            reviewer_calls[step["model"]] = reviewer_calls.get(step["model"], 0) + 1
    status = {"name": name, "schema_version": 1, "state": state, "profile": "production", "tests_passed": tests_passed, "review_verdict": verdict,
              "reason": outcome.reason, "failing_tests": sorted(outcome.failing), "steps": steps, "coder_calls": coder_calls, "reviewer_calls": reviewer_calls, "evidence": evidence,
              "updated": _now()}
    if state == "review-open" and outcome.findings:
        status["findings"] = list(outcome.findings)            # the stripped FIX FIRST findings still open on the kept (green) candidate
    if models is not None and models.contract_file:
        status["contract_check"] = models.contract_file        # the contract evidence, whatever the end state
    status = _scrubbed_obj(status, scrub_keys)               # the reason can carry a provider's error text: scrub the whole status before it is saved
    atomic_json.write_text_atomic(status_dir_job / "status.json", json.dumps(status, indent=2))   # I58: the final status is replaced, never rewritten in place
    event_log.emit(status_dir_job, "finish", state=state, reason=status["reason"])
    print(f"{name}: {state}")
    return _scrubbed_obj({"name": name, "state": state, "round": len(steps), "tests_passed": tests_passed, "review_verdict": verdict, "rounds": steps,
                          "coder_calls": coder_calls, "reviewer_calls": reviewer_calls, "coder_budget": None, "evidence": evidence,
                          "updated": status["updated"]}, scrub_keys)
