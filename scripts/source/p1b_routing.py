"""P1b routing (final roster, 2026-11-02): the production ladder, per-file lineage, the review panel (North first, then GLM; the free writer that
did not write the code takes North's place when North wrote it or is down), the FIX FIRST evidence rule, the repair-step gate, job-size and
settings checks, and the unit deadline. Pure logic: models are injected through a small interface, so every rule runs with scripted fake models
and no network.

Injected `models` interface:
  call(model_id, mode, files) -> dict {path: text} for mode write/repair; str for mode review/review-reask. Raises ModelFailed when the
      provider is down or the repair step is unavailable (its `reason` names the escalation), BadReply when the reply cannot be used, and
      BudgetStop when a GLM review would pass the review ceiling. GLM only ever reviews; the repair step is called with the name ANTHROPIC.
  write(reply) -> puts a validated reply's files on disk (walk calls it after take() accepts the reply, before tests).
  tests(files) -> Result (green, failing test ids, failing files, systemic).
  restore() -> puts the job's original files back before a fresh write.
  package -> {"files": {path: lines}, "spec_count": n}: the review package of the last review call (evidence_ok checks a FIX FIRST against it).

Only the roster's exact IDs appear here. The owner's roster is read from openrouter_call, never copied, so there is one place to change it."""
import json
import re
import time
from collections import namedtuple
from pathlib import Path

import openrouter_call as oc

def _m(role):
    """The roster model for a role, read from the active profile at call time."""
    return oc.roster_state()["ROLES"][role]


_ROLE_NAMES = {"DOTS": "writer_a", "XING": "writer_b", "NORTH": "reviewer_b", "GLM": "reviewer_a"}


def __getattr__(name):
    """DOTS, XING, NORTH and GLM are read from the active profile's roster at each access (PEP 562)."""
    if name in _ROLE_NAMES:
        return _m(_ROLE_NAMES[name])
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
ANTHROPIC = "anthropic"             # the repair step's name in the lineage and the steps; its call lives in the isolated repair module
REPAIR_TIMEOUT_S = 900              # the repair step's call timeout (the repair module's TIMEOUT_S): a repair starts only when this much of the unit deadline is left
DEADLINES_MIN = (60, 120)
UNIT_LINES, FILE_LINES = 1500, 800
FREE_REQUESTS = 40


def budget_for(profile=None):
    """The job budget the profile requires: review_usd is exactly 0 under test and exactly 0.05 under production (owner 2026-11-02)."""
    return {"free_requests": FREE_REQUESTS, "review_usd": oc.JOB_REVIEW_USD_BY_PROFILE[profile or oc.PROFILE], "paid_usd": 0}
REPAIR_CALLS = 2                    # per job: at most two repair-step calls, and a third is never attempted
TRIM_CHARS = 32000                  # pytest output fed back to a writer: about 8K tokens
LOCALIZED_TESTS, LOCALIZED_FILES = 3, 2

Result = namedtuple("Result", "green failing failing_files systemic")
Outcome = namedtuple("Outcome", "status reason")
FILE_LINE = re.compile(r"([\w./\\-]+\.\w+):(\d+)")
CITE = re.compile(r"\b(SPEC|CHECK) (\d+)\b")
CHECKS = 5                          # CHECK 1 to 5 in the review package
LINEAGE_SCHEMA_VERSION = 1
VERDICT = re.compile(r"^## Verdict: (READY|FIX FIRST)$")


class ModelFailed(Exception):
    """A provider refused or stayed down for this call. `reason` is the escalation reason a repair-step failure carries (None otherwise)."""

    def __init__(self, message="", reason=None):
        super().__init__(message)
        self.reason = reason


class JobRefused(ValueError):
    pass


class JobTooLarge(ValueError):
    pass


class Blocked(Exception):
    """Mixed or missing lineage, or a lineage row with no reviewer table entry."""


class BadReply(Exception):
    """A reply that cannot be used: outside the job's files, or missing one on a fresh write."""


class BudgetStop(Exception):
    """A GLM review would pass the job's review ceiling: nothing was sent. The job ends budget-exceeded."""


class Lineage:
    """Per job file: the writer that last wrote it, the origin (the writer of the last fresh write), and the chain of writers since that fresh
    write ("anthropic" for a repair step)."""

    def __init__(self):
        self._rows = {}

    def set(self, path, last, origin, chain=None):
        self._rows[path] = {"last": last, "origin": origin, "chain": list(chain) if chain is not None else [origin]}

    def get(self, path):
        return self._rows.get(path)

    def save(self, path):
        """The file holds one row per job file, plus "schema_version": 1 at the top level (a file with no version is read as version 1)."""
        Path(path).write_text(json.dumps(dict(self._rows, schema_version=LINEAGE_SCHEMA_VERSION), indent=2, sort_keys=True), encoding="utf-8")

    @classmethod
    def load(cls, path):
        lineage = cls()
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        version = data.pop("schema_version", LINEAGE_SCHEMA_VERSION)
        if version != LINEAGE_SCHEMA_VERSION:
            raise ValueError(f"lineage schema_version {version!r} is not {LINEAGE_SCHEMA_VERSION}")
        lineage._rows = data
        return lineage


def check_production_job(job, profile=None):
    """Role names only; the owner's settings for a production unit. The budget is the profile's (budget_for): test 0, production 0.05."""
    wanted = budget_for(profile)
    if job.get("profile") != "production" or job.get("visibility") != "public":
        raise JobRefused("production jobs must be profile production and visibility public")
    if not isinstance(job.get("fixers"), list) or any(role not in ("writer_a", "writer_b") for role in job["fixers"]):
        raise JobRefused("fixers must be a list of the role names writer_a and writer_b (never a model ID)")
    if job.get("reviewers") != "by_lineage":
        raise JobRefused('reviewers must be "by_lineage"')
    budget = job.get("budget")
    if not isinstance(budget, dict) or budget != wanted or any(isinstance(v, bool) for v in budget.values()):
        raise JobRefused(f"budget must be exactly {wanted}")
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


def backup_writer(origin, excluded=(), writers=None):
    writers = (_m("writer_a"), _m("writer_b")) if writers is None else writers
    """The free writer that did not write the code and is not excluded (down, or already on this panel)."""
    for writer in writers:
        if writer != origin and writer not in excluded:
            return writer
    raise oc.ProviderUnavailable("no second independent reviewer is available (provider-unavailable)")


def panel_for(origin, unavailable=(), specs=None):
    """The reviewers for a job's code whose fresh writer was `origin`, from the roster's reviewer roles in panel order: the free reviewer
    (reviewer_b) first, then reviewer_a (the paid reviewer, or the non_author_writer role of the test profile). A slot takes the free writer
    that did not write the code (backup_writer) when its model is the author, is down, or is the non_author_writer role; a paid slot is never
    replaced. The paid reviewer is last, so it is called only after the first reviewer says READY. The author is never on its own panel."""
    specs = oc.ROLE_SPECS if specs is None else specs
    models = {role: spec["model"] for role, spec in specs.items()}
    writers = (models["writer_a"], models["writer_b"])
    if origin not in writers + (models["reviewer_b"],):
        raise Blocked(f"no reviewer row for code whose origin is {origin}")
    down = set(unavailable)
    panel = []
    for role in oc.REVIEWER_ROLES:
        spec = specs[role]
        taken = set(panel) | down
        if spec["model"] == oc.NON_AUTHOR_WRITER or (not spec["paid"] and (spec["model"] == origin or spec["model"] in down)):
            panel.append(backup_writer(origin, taken, writers))
        else:
            panel.append(spec["model"])
    if origin in panel or len(set(panel)) != len(panel):
        raise Blocked(f"panel for {origin} is not independent: {panel}")
    return panel


def _origin(lineage, files):
    rows = []
    for path in files:
        row = lineage.get(path)
        if row is None:
            raise Blocked(f"no lineage recorded for {path}")
        rows.append(row)
    origins = {row["origin"] for row in rows}
    if len(origins) != 1:
        raise Blocked(f"mixed lineage: the job files have origins {sorted(origins)}; blocked")
    return origins.pop()


def panel_for_files(lineage, files, unavailable=()):
    return panel_for(_origin(lineage, files), unavailable=unavailable)


def _localized(result):
    return not result.systemic and len(result.failing) <= LOCALIZED_TESTS and len(result.failing_files) <= LOCALIZED_FILES


def _gate(result, failing, seconds_left):
    """The repair step runs only for a confirmed defect that both free writers failed on the same test, and only when the unit deadline still
    holds a full repair call."""
    if not result.failing:
        return "no confirmed failing test"
    if _m("writer_a") not in failing or _m("writer_b") not in failing:
        return "Dots and Xing were not both tried"
    if not (failing[_m("writer_a")] & failing[_m("writer_b")]):
        return "Dots and Xing did not fail on the same test"
    if seconds_left < REPAIR_TIMEOUT_S:
        return f"the deadline does not fit a repair call ({seconds_left:.0f} s left, {REPAIR_TIMEOUT_S} s needed)"
    return None


def _why(errors, reviewer):
    """The reason a reviewer's slot failed: its upstream error, or that its reply had no verdict line."""
    return f"reviewer {reviewer} error: {errors[reviewer]}" if errors.get(reviewer) else f"reviewer {reviewer} gave no verdict line"


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


def _verdict(models, reviewer, files):
    """(kind, text, error) for one reviewer call. A failed call is INVALID with its upstream error; BudgetStop and Blocked propagate."""
    try:
        kind, text = _review_one(models, reviewer, files)
    except ModelFailed as exc:
        return "INVALID", "", str(exc)
    return kind, text, ""


def _review(files, models, lineage, down):
    try:
        origin = _origin(lineage, files)
        panel = panel_for(origin, unavailable=down)
    except Blocked as exc:
        return Outcome("blocked", str(exc))
    except oc.ProviderUnavailable as exc:
        return Outcome("provider-unavailable", str(exc))
    used = set(panel) | set(down)
    ready, errors = [], {}
    try:
        for slot in panel:                              # North (or its backup) first; GLM only after that READY
            kind, text, error = _verdict(models, slot, files)
            if kind == "READY":
                ready.append(slot)
                continue
            if kind == "FIX":
                return Outcome("review-fix", f"{slot}: {text.strip()}")
            if kind == "UNSUPPORTED":
                return Outcome("review-fix", f"{slot}: FIX FIRST without valid evidence (file:line and SPEC n or CHECK n): {text.strip()}")
            errors[slot] = error
            if slot in oc.PAID:                         # the paid reviewer must answer READY: there is no substitute for it
                return Outcome("provider-unavailable", f"paid review did not return READY ({_why(errors, slot)})")
            try:
                backup = backup_writer(origin, used)
            except oc.ProviderUnavailable as exc:
                return Outcome("provider-unavailable", f"{exc} ({_why(errors, slot)})")
            used.add(backup)
            kind, text, error = _verdict(models, backup, files)
            if kind == "READY":
                ready.append(f"{backup} (backup for {slot})")
                continue
            if kind == "FIX":
                return Outcome("review-fix", f"{backup}: {text.strip()}")
            if kind == "UNSUPPORTED":
                return Outcome("review-fix", f"{backup}: FIX FIRST without valid evidence (file:line and SPEC n or CHECK n): {text.strip()}")
            errors[backup] = error
            return Outcome("provider-unavailable", f"backup reviewer {backup} did not return READY ({_why(errors, slot)}; {_why(errors, backup)})")
    except Blocked as exc:                              # the package is too large: no reviewer call was made for it
        return Outcome("blocked", str(exc))
    return Outcome("passed", "READY from: " + ", ".join(ready))


def walk(job, models, lineage, clock=time.monotonic):
    """The production ladder for one unit. Returns Outcome(status, reason). Statuses: passed, review-fix, escalate, blocked,
    provider-unavailable, budget-exceeded, deadline."""
    started = clock()
    files = list(job["files"])
    wanted = frozenset(files)
    limit = job["deadline_min"] * 60
    failing, down = {}, set()

    def remaining():
        return limit - (clock() - started)

    def late():
        return remaining() < 0

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
            keep = bool(keep_origin and previous)
            origin = previous["origin"] if keep else model
            chain = (list(previous["chain"]) if keep else []) + [model]
            lineage.set(path, model, origin, chain)

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
        result = attempt(_m("writer_a"), "write")
    except ModelFailed:
        down.add(_m("writer_a"))
        result = None
    except BadReply:
        result = None

    if result is not None and not result.green and _localized(result):
        if late():
            return deadline
        try:
            result = attempt(_m("writer_a"), "repair")
        except ModelFailed:
            down.add(_m("writer_a"))
            result = None
        except BadReply:
            result = None

    fresh_writer = None
    if result is None or not result.green:
        if late():
            return deadline
        models.restore()
        for writer in (_m("writer_b"), _m("reviewer_b")):             # North writes only when Xing is down
            try:
                result = attempt(writer, "write", full=True)
                fresh_writer = writer
                break
            except ModelFailed:
                down.add(writer)
            except BadReply as exc:
                return Outcome("escalate", str(exc))
        else:
            return Outcome("provider-unavailable", "Xing and North both failed the fresh write")

    if not result.green and fresh_writer == _m("writer_b"):
        if late():
            return deadline
        try:
            result = attempt(_m("writer_b"), "repair")
        except ModelFailed:
            return Outcome("escalate", "Xing repair is unavailable")
        except BadReply as exc:
            return Outcome("escalate", str(exc))

    repairs = 0
    while not result.green:
        if late():
            return deadline
        if repairs == REPAIR_CALLS:
            return Outcome("escalate", f"repair calls used up ({REPAIR_CALLS}) and still red")
        why = _gate(result, failing, remaining())
        if why:
            return Outcome("escalate", f"repair gate: {why}")
        repairs += 1
        try:
            result = attempt(ANTHROPIC, "repair", keep_origin=True)
        except ModelFailed as exc:
            return Outcome("escalate", exc.reason or "repair unavailable")
        except BadReply as exc:
            return Outcome("escalate", str(exc))

    try:
        return _review(files, models, lineage, down)
    except BudgetStop as exc:
        return Outcome("budget-exceeded", str(exc))
