"""Shared plumbing for the API: the engine's import path, the repo and SATVIK_HOME folders, the error envelope, atomic JSON files, and the
Idempotency-Key wrapper over the loop's api_idempotency store. Stdlib only; the engine modules are imported from scripts/source/."""
import json
import os
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "scripts" / "source"
if str(ENGINE) not in sys.path:
    sys.path.insert(0, str(ENGINE))

import loop_home  # noqa: E402  (after the path is set)
from api_idempotency import IdempotencyStore  # noqa: E402


class ApiError(Exception):
    """A contract error: HTTP status, a stable code, a human message and optional details (the error envelope)."""

    def __init__(self, status, code, message="", details=None):
        super().__init__(message or code)
        self.status = status
        self.code = code
        self.message = message or code
        self.details = details or {}

    def body(self):
        return {"error": {"code": self.code, "message": self.message, "details": self.details}}


def repo():
    """The repo root: SATVIK_REPO when set (tests and a second checkout), else the checkout this file lives in."""
    raw = os.environ.get("SATVIK_REPO", "").strip()
    return Path(raw) if raw else ROOT


def home():
    """SATVIK_HOME as a folder outside every worktree. Unset or refused: 503 engine-unavailable (the message says why)."""
    try:
        return loop_home.home(loops=1)
    except loop_home.LoopHomeError as exc:
        raise ApiError(503, "engine-unavailable", str(exc))


def read_json(path, default=None):
    """A JSON object from a file, or `default` when it is missing, unreadable or caught mid-write (read again on the next poll)."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default
    return data


def atomic_write_json(path, data):
    """Writes JSON through a temp file in the same folder, then a rename: a reader sees the old file or the new one, never half of one."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, sort_keys=True)
            fh.write("\n")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


_MUTATION_LOCK = threading.RLock()     # one mutation at a time in this process: a double click cannot run two actions


def _store(home_dir):
    """The loop's idempotency store (scripts/source/api_idempotency.py): one file per key under SATVIK_HOME/api/idempotency/."""
    return IdempotencyStore(Path(home_dir) / "api" / "idempotency")


def respond(home_dir, key, fn):
    """Runs one mutation at most once per Idempotency-Key. fn() -> (status, body). A repeat with the same key returns the first final
    response and runs nothing. A final response is anything below 500 (a validation or state error included); a 5xx is not stored, so the
    caller may try again. Returns (status, body)."""
    def once():
        try:
            return fn()
        except ApiError as exc:
            if exc.status >= 500:
                raise
            return exc.status, exc.body()
    with _MUTATION_LOCK:
        if key is None:
            return once()
        store = _store(home_dir)
        try:
            prior = store.get(key)
        except ValueError as exc:
            raise ApiError(400, "invalid-idempotency-key", str(exc))
        if prior is not None:
            return prior["status"], prior.get("body")
        status, body = once()
        stored = store.put(key, {"status": status, "body": body})
        return stored["status"], stored.get("body")
