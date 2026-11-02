"""P1b routing (2026-11-02, Revision 3): the production escalation ladder, per-file lineage, reviewer panels by lineage, the FIX FIRST evidence
rule, the GLM gate, job-size and settings checks, and the unit deadline. Pure logic: models are injected through a small interface, so every
rule runs with scripted fake models and no network.

Injected `models` interface:
  call(model_id, mode, files) -> dict {path: text} for mode write/repair; str for mode review/review-reask. Raises ModelFailed when the
      provider is down, BadReply when the reply cannot be used. Only the adapter's roster IDs are used (oc.ROLES), and a paid ID is only ever
      given to the repair step.
  write(reply) -> puts a validated reply's files on disk (walk calls it after take() accepts the reply, before tests).
  tests(files) -> Result (green, failing test ids, failing files, systemic).
  restore() -> puts the job's original files back before a fresh write.
  paid_fits() -> True when the next GLM call's reserved worst case still fits the $0.15 job ceiling.
  package -> {"files": {path: lines}, "spec_count": n}: the review package of the last review call (evidence_ok checks a FIX FIRST against it).

Only the roster's exact IDs appear here. The owner's roster is read from openrouter_call, never copied, so there is one place to change it."""
import json
import re
import time
from collections import namedtuple
from pathlib import Path

import openrouter_call as oc

DOTS, XING, NORTH, GLM = oc.ROLES["writer_a"], oc.ROLES["writer_b"], oc.ROLES["reviewer"], oc.ROLES["repair"]
GEMMA = "GEMMA"                     # placeholder in a panel row: the first enabled Gemma backup, chosen at review time
DEADLINES_MIN = (60, 120)
UNIT_LINES, FILE_LINES = 1500, 800
FREE_REQUESTS, PAID_USD = 40, 0.15
GLM_ROUNDS = 2
TRIM_CHARS = 32000                  # pytest output fed back to a writer: about 8K tokens
LOCALIZED_TESTS, LOCALIZED_FILES = 3, 2

Result = namedtuple("Result", "green failing failing_files systemic")
Outcome = namedtuple("Outcome", "status reason")
FILE_LINE = re.compile(r"([\w./\\-]+\.\w+):(\d+)")
CITE = re.compile(r"\b(SPEC|CHECK) (\d+)\b")
CHECKS = 5                          # CHECK 1 to 5 in the review package
VERDICT = re.compile(r"^## Verdict: (READY|FIX FIRST)$")


class ModelFailed(Exception):
    """A provider refused or stayed down for this call."""


class JobRefused(ValueError):
    pass


class JobTooLarge(ValueError):
    pass


class Blocked(Exception):
    """Mixed or missing lineage, or a lineage row with no reviewer table entry."""


class BadReply(Exception):
    """A reply that cannot be used: outside the job's files, or missing one on a fresh write."""


class Lineage:
    """Per job file: the model ID that last wrote it, and the writer it came from (the origin differs only after a GLM repair)."""

    def __init__(self):
        self._rows = {}

    def set(self, path, last, origin):
        self._rows[path] = {"last": last, "origin": origin}

    def get(self, path):
        return self._rows.get(path)

    def save(self, path):
        Path(path).write_text(json.dumps(self._rows, indent=2, sort_keys=True), encoding="utf-8")

    @classmethod
    def load(cls, path):
        lineage = cls()
        lineage._rows = json.loads(Path(path).read_text(encoding="utf-8"))
        return lineage


def check_production_job(job):
    """Role names only; the owner's settings for a production unit."""
    if job.get("profile") != "production" or job.get("visibility") != "public":
        raise JobRefused("production jobs must be profile production and visibility public")
    if not isinstance(job.get("fixers"), list) or any(role not in ("writer_a", "writer_b") for role in job["fixers"]):
        raise JobRefused("fixers must be a list of the role names writer_a and writer_b (never a model ID)")
    if job.get("reviewers") != "by_lineage":
        raise JobRefused('reviewers must be "by_lineage"')
    budget = job.get("budget")
    if not isinstance(budget, dict) or set(budget) != {"free_requests", "paid_usd"} or budget["free_requests"] != FREE_REQUESTS:
        raise JobRefused(f"budget must be free_requests {FREE_REQUESTS} and paid_usd 0 (no paid call) or {PAID_USD}")
    paid = budget["paid_usd"]
    if isinstance(paid, bool) or paid not in (0, PAID_USD):   # the owner's two values: 0 (free pilot, no paid call) or the $0.15 ceiling
        raise JobRefused(f"budget must be free_requests {FREE_REQUESTS} and paid_usd 0 (no paid call) or {PAID_USD}")
    if job.get("deadline_min") not in DEADLINES_MIN:
        raise JobRefused(f"deadline_min must be one of {DEADLINES_MIN}")


def check_job_size(lines):
    """lines: {editable path: line count}. At most 800 lines per file and 1,500 per unit."""
    for path, count in lines.items():
        if count > FILE_LINES:
            raise JobTooLarge(f"{path} has {count} lines; the limit is {FILE_LINES} per file (split it first)")
    total = sum(lines.values())
    if total > UNIT_LINES:
        raise JobTooLarge(f"the unit has {total} editable lines; the limit is {UNIT_LINES} per unit (the planner splits it)")


def _path(raw):
    return raw.replace("\\", "/").removeprefix("./")


def evidence_ok(text, package):
    """A FIX FIRST counts only with BOTH: a file:line naming a file in the package, with a line no greater than that file's length, and a
    SPEC n (n within the spec's requirement count) or CHECK n (n from 1 to 5). `package` is {"files": {path: lines}, "spec_count": n}."""
    files, spec_count = package["files"], package["spec_count"]
    file_ok = any(_path(path) in files and 1 <= int(line) <= files[_path(path)] for path, line in FILE_LINE.findall(text))
    cite_ok = any(1 <= int(num) <= (spec_count if kind == "SPEC" else CHECKS) for kind, num in CITE.findall(text))
    return file_ok and cite_ok


def verdict_of(text):
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    match = VERDICT.match(lines[-1]) if lines else None
    return match.group(1) if match else None


def trim_tail(text, limit=TRIM_CHARS):
    return text[-limit:] if len(text) > limit else text


def _gemma_for(exclude=()):
    if not oc.REVIEWER_BACKUP_ENABLED:
        raise oc.ProviderUnavailable("Gemma reviewer backup is disabled: no reviewer for this slot (provider-unavailable)")
    for model in oc.REVIEWER_BACKUP:
        if model not in exclude:
            return model
    raise oc.ProviderUnavailable("no Gemma backup is left for this slot (provider-unavailable)")


def panel_for(author, origin=None, unavailable=()):
    """The reviewers for code last written by `author` (lineage table, Revision 3). Identity decides: the author is never on its panel."""
    down = set(unavailable)
    if author == GLM and origin == DOTS:
        base = [XING, NORTH]
    elif author == GLM and origin == XING:
        base = [DOTS, NORTH]
    elif author == DOTS:
        base = [XING, NORTH]
    elif author == XING:
        base = [NORTH, GEMMA] if DOTS in down else [DOTS, NORTH]
    elif author == NORTH:
        base = [DOTS, GEMMA]
    else:
        raise Blocked(f"no reviewer row for code last written by {author}")
    panel = []
    for slot in base:
        if slot == GEMMA or slot in down:
            model = _gemma_for(down | set(panel))
        else:
            model = slot
        panel.append(model)
    if author in panel or len(set(panel)) != len(panel):
        raise Blocked(f"panel for {author} is not independent: {panel}")
    return panel


def panel_for_files(lineage, files, unavailable=()):
    rows = []
    for path in files:
        row = lineage.get(path)
        if row is None:
            raise Blocked(f"no lineage recorded for {path}")
        rows.append(row)
    lasts = {row["last"] for row in rows}
    if len(lasts) != 1:
        raise Blocked(f"mixed lineage: the job files were last written by {sorted(lasts)}; blocked")
    origins = {row["origin"] for row in rows}
    return panel_for(lasts.pop(), origin=origins.pop() if len(origins) == 1 else None, unavailable=unavailable)


def _localized(result):
    return not result.systemic and len(result.failing) <= LOCALIZED_TESTS and len(result.failing_files) <= LOCALIZED_FILES


def _gate(result, failing, models):
    """GLM only for a confirmed defect that both free writers failed on, and only if the reserved worst case fits the job ceiling."""
    if not result.failing:
        return "no confirmed failing test"
    if DOTS not in failing or XING not in failing:
        return "Dots and Xing were not both tried"
    if not (failing[DOTS] & failing[XING]):
        return "Dots and Xing did not fail on the same test"
    if not models.paid_fits():
        return "the budget does not fit: the next call would pass the job's paid ceiling"
    return None


def _why(errors, reviewer):
    """The reason a reviewer's slot failed: its upstream error, or that its reply had no verdict line."""
    return f"reviewer {reviewer} error: {errors[reviewer]}" if reviewer in errors else f"reviewer {reviewer} gave no verdict line"


def _review_one(models, reviewer, files):
    """(kind, text). READY. FIX: a FIX FIRST with valid evidence. UNSUPPORTED: a FIX FIRST without valid evidence, even after one re-ask; it
    stays open, and neither a backup nor a later READY overrides it. INVALID: the call failed or the reply has no verdict line, so the backup
    takes the slot (no re-ask). `models.package` is the package the review was given (the runner sets it on each review call)."""
    text = models.call(reviewer, "review", files)
    verdict = verdict_of(text)
    if verdict == "READY":
        return "READY", text
    if verdict is None:
        return "INVALID", text
    if evidence_ok(text, models.package):
        return "FIX", text
    try:
        again = models.call(reviewer, "review-reask", files)
    except ModelFailed:
        return "UNSUPPORTED", text
    if verdict_of(again) == "FIX FIRST" and evidence_ok(again, models.package):
        return "FIX", again
    return "UNSUPPORTED", again if again.strip() else text


def _review(files, models, lineage, down):
    try:
        panel = panel_for_files(lineage, files, unavailable=down)
    except Blocked as exc:
        return Outcome("blocked", str(exc))
    except oc.ProviderUnavailable as exc:
        return Outcome("provider-unavailable", str(exc))
    used = set(panel) | set(down)
    ready, errors = [], {}
    for reviewer in panel:
        try:
            kind, text = _review_one(models, reviewer, files)
        except ModelFailed as exc:
            kind, text = "INVALID", ""
            errors[reviewer] = str(exc)
        except Blocked as exc:                    # the package is too large: no reviewer call was made for it
            return Outcome("blocked", str(exc))
        if kind == "READY":
            ready.append(reviewer)
            continue
        if kind in ("FIX", "UNSUPPORTED"):
            why = "" if kind == "FIX" else "FIX FIRST without valid evidence (file:line and SPEC n or CHECK n): "
            return Outcome("review-fix", f"{reviewer}: {why}{text.strip()}")
        try:                                      # INVALID: the call failed or the reply had no verdict line
            backup = _gemma_for(used)
        except oc.ProviderUnavailable as exc:
            return Outcome("provider-unavailable", f"{exc} ({_why(errors, reviewer)})")
        used.add(backup)
        try:
            kind, text = _review_one(models, backup, files)
        except ModelFailed as exc:
            kind, text = "INVALID", ""
            errors[backup] = str(exc)
        except Blocked as exc:
            return Outcome("blocked", str(exc))
        if kind in ("FIX", "UNSUPPORTED"):
            return Outcome("review-fix", f"{backup}: {text.strip()}")
        if kind != "READY":
            return Outcome("provider-unavailable", f"backup reviewer {backup} did not return READY with evidence ({_why(errors, reviewer)}; {_why(errors, backup)})")
        ready.append(f"{backup} (backup for {reviewer})")
    return Outcome("passed", "READY from: " + ", ".join(ready))


def walk(job, models, lineage, clock=time.monotonic):
    """The production ladder for one unit. Returns Outcome(status, reason). Statuses: passed, review-fix, escalate, blocked,
    provider-unavailable, deadline."""
    started = clock()
    files = list(job["files"])
    wanted = frozenset(files)
    limit = job["deadline_min"] * 60
    failing, down = {}, set()

    def late():
        return clock() - started > limit

    def take(reply, model, full, keep_origin):
        if not isinstance(reply, dict) or not reply:
            raise BadReply(f"{model} reply has no FILE blocks")
        extra = set(reply) - wanted
        if extra:
            raise BadReply(f"{model} reply names files outside the job: {sorted(extra)}")
        if full and set(reply) != wanted:
            raise BadReply(f"{model} fresh reply is missing job files: {sorted(wanted - set(reply))}")
        for path in reply:
            previous = lineage.get(path)
            origin = previous["origin"] if (keep_origin and previous) else model
            lineage.set(path, model, origin)

    def attempt(model, mode, full=False, keep_origin=False):
        reply = models.call(model, mode, files)
        take(reply, model, full, keep_origin)
        models.write(reply)                      # validated files go to disk before the tests run
        result = models.tests(files)
        failing[model] = result.failing
        return result

    deadline = Outcome("deadline", "the unit deadline passed; stopped between rounds")
    if late():
        return deadline

    try:
        result = attempt(DOTS, "write")
    except ModelFailed:
        down.add(DOTS)
        result = None
    except BadReply:
        result = None

    if result is not None and not result.green and _localized(result):
        if late():
            return deadline
        try:
            result = attempt(DOTS, "repair")
        except ModelFailed:
            down.add(DOTS)
            result = None
        except BadReply:
            result = None

    fresh_writer = None
    if result is None or not result.green:
        if late():
            return deadline
        models.restore()
        for writer in (XING, NORTH):
            try:
                result = attempt(writer, "write", full=True)
                fresh_writer = writer
                break
            except ModelFailed:
                down.add(writer)
            except BadReply as exc:
                return Outcome("escalate", str(exc))
        else:
            return Outcome("provider-unavailable", "Xing and North Mini Code both failed the fresh write")

    if not result.green and fresh_writer == XING:
        if late():
            return deadline
        try:
            result = attempt(XING, "repair")
        except ModelFailed:
            return Outcome("escalate", "Xing repair is unavailable")
        except BadReply as exc:
            return Outcome("escalate", str(exc))

    rounds = 0
    while not result.green:
        if late():
            return deadline
        if rounds == GLM_ROUNDS:
            return Outcome("escalate", f"GLM rounds used up ({GLM_ROUNDS}) and still red")
        why = _gate(result, failing, models)
        if why:
            return Outcome("escalate", f"GLM gate: {why}")
        rounds += 1
        try:
            result = attempt(GLM, "repair", keep_origin=True)
        except ModelFailed:
            return Outcome("escalate", "GLM repair is unavailable")
        except BadReply as exc:
            return Outcome("escalate", str(exc))

    return _review(files, models, lineage, down)
