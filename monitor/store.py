"""Shared plumbing for the API: the engine's import path, the repo and SATVIK_HOME folders, the error envelope, atomic JSON files, and the
Idempotency-Key wrapper over the loop's api_idempotency store. Stdlib only; the engine modules are imported from scripts/source/."""
import json
import os
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "scripts" / "source"
if str(ENGINE) not in sys.path:
    sys.path.insert(0, str(ENGINE))

import atomic_json  # noqa: E402  (after the path is set)
import loop_home  # noqa: E402
from api_idempotency import OperationConflict, OperationStore  # noqa: E402


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
    """Writes JSON through the engine's one atomic writer (atomic_json, I58): a temp file in the same folder, then a replace, with bounded
    retries on a Windows sharing violation. A reader sees the old file or the new one, never half of one."""
    atomic_json.write_json_atomic(path, data)


# Two locks (I60). _STATE_LOCK is held only for the short transitions: a mutation's intent is recorded under it before its effects, and its
# outcome after. _EFFECTS_LOCK is held while a fast mutation does its work, so two fast mutations never interleave. A slow mutation (a model
# call: verify, verify-reasoning, refresh) does not take _EFFECTS_LOCK, so STOP and the reads answer while it runs.
_STATE_LOCK = threading.RLock()
_EFFECTS_LOCK = threading.RLock()


def _store(home_dir):
    """The API's idempotency records (scripts/source/api_idempotency.py OperationStore): one file per key under SATVIK_HOME/api/idempotency/."""
    return OperationStore(Path(home_dir) / "api" / "idempotency")


def respond(home_dir, key, fn, fingerprint=None, slow=False):
    """Runs one mutation at most once per Idempotency-Key (codex I59, the protocol is in OperationStore). fn() -> (status, body).

    The intent (key + request fingerprint, state started) is recorded before fn runs. A repeat with the same key replays the recorded final
    response, or answers 409 operation-in-progress (started, no outcome), 409 idempotency-record-corrupt, or 422 idempotency-key-reused
    (another request). A final response is anything below 500 (a validation or state error included). A 5xx, or an exception, aborts the
    intent, so the caller may try again. Returns (status, body)."""
    def once():
        try:
            return fn()
        except ApiError as exc:
            if exc.status >= 500:
                raise
            return exc.status, exc.body()

    def work():
        if slow:
            return once()
        with _EFFECTS_LOCK:
            return once()

    if key is None:
        return work()
    store = _store(home_dir)
    try:
        with _STATE_LOCK:
            decision, recorded = store.begin(key, fingerprint or "")
    except ValueError as exc:
        raise ApiError(400, "invalid-idempotency-key", str(exc))
    except OperationConflict as exc:
        raise ApiError(exc.status, exc.code, exc.message)
    if decision == "replay":
        return recorded
    try:
        status, body = work()
    except BaseException:
        with _STATE_LOCK:
            store.abort(key)
        raise
    if status >= 500:
        with _STATE_LOCK:
            store.abort(key)
        return status, body
    with _STATE_LOCK:
        return store.finish(key, fingerprint or "", status, body)
