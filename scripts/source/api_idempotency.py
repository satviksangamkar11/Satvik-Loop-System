import json
import os
import re
import tempfile
import time


_KEY_RE = re.compile(r'^[A-Za-z0-9_-]{8,128}$')


class IdempotencyStore:
    """A simple idempotency store that keeps one JSON file per key."""

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
        return isinstance(key, str) and _KEY_RE.match(key) is not None

    def _read(self, path):
        """Read and parse a stored file. Returns None if absent or unreadable."""
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (OSError, ValueError):
            return None

    def _write(self, path, data):
        """Atomically write data to path via a temp file + os.replace."""
        fd, tmp_path = tempfile.mkstemp(dir=self._folder)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump(data, f)
            os.replace(tmp_path, path)
        except BaseException:
            try:
                os.remove(tmp_path)
            except OSError:
                pass
            raise

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
