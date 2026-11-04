import hashlib
import json
import os
import re
import sys
import time

try:
    import atomic_json
except ImportError:  # loaded by path (the loop's acceptance tests): this folder is not on sys.path yet
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import atomic_json


_KEY_RE = re.compile(r'^[A-Za-z0-9_-]{8,128}$')


def _is_valid_key_text(key):
    return isinstance(key, str) and _KEY_RE.match(key) is not None


class IdempotencyStore:
    """The loop's original one-file-per-key store: first write wins, a corrupt or undated record reads as absent, 24 h expiry. Its tests
    (pieces/test_api_idempotency.py, the loop's acceptance tests) pin that behaviour, so it stays as it is. The API uses OperationStore."""

    def __init__(self, folder, ttl_s=86400, clock=time.time):
        self._folder = folder
        self._ttl_s = ttl_s
        self._clock = clock
        if not os.path.isdir(folder):
            os.makedirs(folder)

    def _path_for(self, key):
        return os.path.join(self._folder, key + '.json')

    @staticmethod
    def _is_valid_key(key):
        return _is_valid_key_text(key)

    def _read(self, path):
        """Read and parse a stored file. Returns None if absent or unreadable."""
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (OSError, ValueError):
            return None

    def _write(self, path, data):
        """Atomically write data to path: the one atomic writer (atomic_json, I58)."""
        atomic_json.write_text_atomic(path, json.dumps(data))

    def get(self, key):
        if not self._is_valid_key(key):
            raise ValueError("Invalid key: %r" % (key,))
        data = self._read(self._path_for(key))
        if data is None:
            return None
        ts = data.get('ts')
        if ts is None:
            return None
        if self._clock() - ts > self._ttl_s:
            return None
        return data.get('response')

    def put(self, key, response):
        if not self._is_valid_key(key):
            raise ValueError("Invalid key: %r" % (key,))
        path = self._path_for(key)
        data = self._read(path)
        if data is not None:
            ts = data.get('ts')
            if ts is not None and self._clock() - ts <= self._ttl_s:
                # First write wins: keep the existing unexpired response.
                return data.get('response')
        # Either absent, expired, or unreadable: store the new response.
        new_data = {'ts': self._clock(), 'response': response}
        self._write(path, new_data)
        return response


# ---------------------------------------------------------------- operation intents (I59)

class OperationConflict(Exception):
    """A request the store refuses before any effect: a status, a stable code and a message (the API's error envelope)."""

    def __init__(self, status, code, message):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def request_fingerprint(method, path, body):
    """The request's identity for its Idempotency-Key: method, path and the JSON body in canonical form (sorted keys)."""
    canonical = json.dumps({"method": method, "path": path, "body": body}, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class OperationStore:
    """Idempotency records with an operation intent (codex I59). Before a mutation's first effect, its key is recorded as `started` with the
    request fingerprint. When the mutation answers, the record becomes `finished` with the response. So a retry:

      - finished, same fingerprint, inside retention: replays the recorded response; nothing runs;
      - finished, same fingerprint, past retention: the key is free again, and the request runs as new;
      - started (the process stopped before the outcome was recorded): 409 operation-in-progress; nothing runs. It is not expired:
        an operator checks what the effect was and removes the record file (api/idempotency/<key>.json) to free the key;
      - a different fingerprint on the same key: 422 idempotency-key-reused; nothing runs;
      - a record that cannot be read or does not match the format: 409 idempotency-record-corrupt; nothing runs (never treated as absent).

    A mutation that answers with a status below 500 is finished (a 4xx validation or state error is a final outcome). One that raises, or
    answers 5xx, is aborted: its intent is removed and the same key may run again, as the API has always allowed. Records are one JSON file
    per key, written through atomic_json. Single worker only (docs/API_CONTRACT.md section 0)."""

    RETENTION_S = 86400

    def __init__(self, folder, retention_s=RETENTION_S, clock=time.time):
        self._folder = folder
        self._retention = retention_s
        self._clock = clock
        os.makedirs(folder, exist_ok=True)

    def path_for(self, key):
        if not _is_valid_key_text(key):
            raise ValueError("Invalid key: %r" % (key,))
        return os.path.join(self._folder, key + '.json')

    @staticmethod
    def _corrupt(key, why):
        return OperationConflict(409, "idempotency-record-corrupt",
                                 f"the stored record for this Idempotency-Key cannot be read ({why}); it is not treated as absent. "
                                 "Check the effect, then remove its record file to free the key")

    def _load(self, key):
        path = self.path_for(key)
        try:
            with open(path, 'r', encoding='utf-8') as fh:
                text = fh.read()
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise self._corrupt(key, f"unreadable: {type(exc).__name__}")
        try:
            rec = json.loads(text)
        except ValueError:
            raise self._corrupt(key, "not JSON")
        if not isinstance(rec, dict) or rec.get("key") != key or not isinstance(rec.get("fingerprint"), str):
            raise self._corrupt(key, "not a record of this key")
        state = rec.get("state")
        if state == "started":
            return rec
        if state == "finished" and isinstance(rec.get("status"), int) and "body" in rec \
                and isinstance(rec.get("finished_at"), (int, float)) and not isinstance(rec.get("finished_at"), bool):
            return rec
        raise self._corrupt(key, "unknown state")

    def begin(self, key, fingerprint):
        """Decides a request before its effects. Returns ("run", None) when it must run (its intent is now recorded as started) or
        ("replay", (status, body)) with the recorded outcome. Raises OperationConflict for the refusals above. ValueError: a bad key."""
        path = self.path_for(key)
        rec = self._load(key)
        if rec is not None:
            if rec["fingerprint"] != fingerprint:
                raise OperationConflict(422, "idempotency-key-reused",
                                        "this Idempotency-Key was already used for a different request")
            if rec["state"] == "started":
                raise OperationConflict(409, "operation-in-progress",
                                        "an earlier request with this Idempotency-Key has no recorded outcome: it is still running, or its "
                                        "process stopped before it finished. It is not run again")
            if self._clock() - rec["finished_at"] <= self._retention:
                return "replay", (rec["status"], rec["body"])
        atomic_json.write_json_atomic(path, {"key": key, "fingerprint": fingerprint, "state": "started", "started_at": self._clock()})
        return "run", None

    def finish(self, key, fingerprint, status, body):
        """Records the outcome of a started request. A failure to write it propagates: the intent then stays started (fail closed)."""
        atomic_json.write_json_atomic(self.path_for(key), {"key": key, "fingerprint": fingerprint, "state": "finished", "status": status,
                                                           "body": body, "finished_at": self._clock()})
        return status, body

    def abort(self, key):
        """Removes the intent of a started request that raised or answered 5xx: its key may run again. A record that is not started is left."""
        try:
            rec = self._load(key)
        except OperationConflict:
            return
        if rec is not None and rec["state"] == "started":
            try:
                os.remove(self.path_for(key))
            except FileNotFoundError:
                pass
