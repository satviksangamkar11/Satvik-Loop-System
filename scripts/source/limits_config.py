"""Operator limits (Control Center P2, 2026-11-03): config/limits.json, a small versioned file the API edits (PUT /system/limits).

{"schema_version": 1, "revision": n, "sandbox_slots": 1-4, "review_usd_daily_cap": x, "job_review_usd": y}

The engine reads it at each start, so an edit applies to NEW starts only: launch.Launcher takes its sandbox slots from here (when it was
built without an explicit number), launch.start_refusal and shared_limits take the daily review cap from here. A missing or unreadable file
means the defaults below, which are the values the engine had before this file existed. job_review_usd is stored and bounded here, but the
production budget gate (p0_gates, fix_loop.load_job) still requires the per-job review budget to be exactly 0.05, so the engine does not use
a different figure: it is the ceiling /budget/validate checks a paid budget against. Stdlib only."""
import json
import os
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PATH = REPO / "config" / "limits.json"          # a test points this at a copy
SCHEMA_VERSION = 1
DEFAULTS = {"sandbox_slots": 4, "review_usd_daily_cap": 0.5, "job_review_usd": 0.05}
BOUNDS = {"sandbox_slots": (1, 4), "review_usd_daily_cap": (0.0, 5.0), "job_review_usd": (0.0, 0.05)}


class LimitsError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _valid(data):
    """The file's values when they are usable, else raises LimitsError."""
    if not isinstance(data, dict) or data.get("schema_version") != SCHEMA_VERSION:
        raise LimitsError("schema", "limits.json: schema_version must be 1")
    rev = data.get("revision")
    if not isinstance(rev, int) or isinstance(rev, bool) or rev < 1:
        raise LimitsError("schema", "limits.json: revision must be a whole number of at least 1")
    out = {"schema_version": SCHEMA_VERSION, "revision": rev}
    for key, default in DEFAULTS.items():
        value = data.get(key, default)
        lo, hi = BOUNDS[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not lo <= value <= hi:
            raise LimitsError("out-of-range", f"limits.json: {key} must be between {lo} and {hi}")
        out[key] = int(value) if key == "sandbox_slots" else float(value)
    return out


def load(path=None):
    """The limits as they stand: {schema_version, revision, sandbox_slots, review_usd_daily_cap, job_review_usd}. Missing or unreadable:
    the defaults at revision 0 (so the first PUT writes revision 1)."""
    try:
        data = json.loads(Path(path or PATH).read_text(encoding="utf-8"))
        return _valid(data)
    except (OSError, ValueError, LimitsError):
        return {"schema_version": SCHEMA_VERSION, "revision": 0, **DEFAULTS}


def save(values, path=None):
    """Writes the limits atomically (temp file in the same folder, then a rename). `values` must be a whole valid file."""
    target = Path(path or PATH)
    data = _valid(values)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".limits.", suffix=".tmp", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
            fh.write("\n")
        os.replace(tmp, target)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return data


def sandbox_slots(path=None):
    return load(path)["sandbox_slots"]


def review_usd_daily_cap(path=None):
    return load(path)["review_usd_daily_cap"]


def job_review_usd(path=None):
    return load(path)["job_review_usd"]
