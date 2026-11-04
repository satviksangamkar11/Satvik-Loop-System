"""The one atomic writer for authoritative files (I58, codex audit 2026-11-04): stage config, the API's stores, the idempotency records and every
job's status.json. A reader sees the old file or the new one, never half of either.

The write goes to a temp file beside the target, is flushed to disk, then replaces the target. On Windows the replace fails with a sharing
violation while another process has the target open (PermissionError, WinError 32 or 5): those are retried a bounded number of times
(RETRY_ATTEMPTS x RETRY_DELAY_S, about 2 seconds). A target that stays locked raises SharingViolation: the old file is left as it was and
the temp file is removed. Any other OSError is raised at once. Stdlib only."""
import json
import os
import tempfile
import time
from pathlib import Path

RETRY_ATTEMPTS = 40
RETRY_DELAY_S = 0.05
_SHARING_WINERRORS = (5, 32)          # ERROR_ACCESS_DENIED, ERROR_SHARING_VIOLATION


class SharingViolation(OSError):
    """The target stayed locked by another process for every retry: nothing was written."""


def _sharing_violation(exc: OSError) -> bool:
    return isinstance(exc, PermissionError) or getattr(exc, "winerror", None) in _SHARING_WINERRORS


def replace_retrying(tmp, path, *, replace=None, sleep=None, attempts=RETRY_ATTEMPTS, delay=RETRY_DELAY_S):
    """os.replace(tmp, path) with bounded retries on a sharing violation. Returns the number of retries it needed (0 at once). The
    replace and sleep seams are looked up at call time (tests replace them)."""
    replace = replace or os.replace
    sleep = sleep or time.sleep
    last = None
    for retry in range(attempts):
        try:
            replace(tmp, path)
            return retry
        except OSError as exc:
            if not _sharing_violation(exc):
                raise
            last = exc
            sleep(delay)
    raise SharingViolation(f"{path} stayed locked by another process after {attempts} tries: {last}") from last


def write_bytes_atomic(path, data: bytes, **retry) -> None:
    """Writes bytes to `path` in one replace (see the module doc). `retry` takes the replace/sleep/attempts/delay test seams."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        replace_retrying(tmp, path, **retry)
    finally:
        if os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass


def write_text_atomic(path, text: str, **retry) -> None:
    write_bytes_atomic(path, text.encode("utf-8"), **retry)


def write_json_atomic(path, data, *, sort_keys=True, **retry) -> None:
    """JSON with indent 2 and a final newline, through the atomic writer."""
    write_text_atomic(path, json.dumps(data, indent=2, sort_keys=sort_keys) + "\n", **retry)
