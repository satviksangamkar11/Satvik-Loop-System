"""Shared loop home (Revision 4, WP2): one folder outside every worktree holds all state that two loops could share.

Layout under home():
  lock                 the one OS lock; every read and write of shared state takes it
  leases/<job>.json    pid, started, heartbeat, generation, released
  claims.json          repo-relative path -> [{job, gen, mode}]   (mode "w" exclusive, "r" shared read)
  registry.json        exact-request key -> {job, gen, state, base, result}
  gates.json           gate name -> [{pid, gen, token}]

Rules: a takeover needs a stale lease (heartbeat older than 5 minutes AND a dead pid). Every shared write is refused (Fenced) when the writer's
generation is lower than the stored one. Stdlib only, no network, no model call.
ponytail: one lock file for all state, per-file locks if throughput ever matters; gates have no pacing or ledger yet (WP4 adds them)."""
import json
import os
import re
import secrets
import subprocess
import threading
import time
from contextlib import contextmanager
from pathlib import Path

try:
    import msvcrt
except ImportError:                       # not Windows: fcntl does the same job
    msvcrt = None
    import fcntl

ENV = "SATVIK_HOME"
LOCK_OFFSET = 1 << 30                     # lock byte far beyond any content
STALE_S = 300.0
HEARTBEAT_S = 30.0
LOCK_TIMEOUT_S = 30.0
COUNTER_PREFIX = "counter:"       # registry rows under this prefix are counters, not requests (step 8)
CAPACITY = {}                             # gate name -> slots; default 1
JOB_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,159}$")


class LoopHomeError(RuntimeError):
    pass


class HomeRequired(LoopHomeError):
    pass


class LockTimeout(LoopHomeError):
    pass


class LeaseHeld(LoopHomeError):
    pass


class Fenced(LoopHomeError):
    pass


class LeaseLost(LoopHomeError):
    pass


class RequestRunning(LoopHomeError):
    pass


# ---------------------------------------------------------------- home and processes
def _inside(child, parent) -> bool:
    c = os.path.normcase(os.path.realpath(str(child)))
    p = os.path.normcase(os.path.realpath(str(parent)))
    return c == p or c.startswith(p.rstrip(os.sep) + os.sep)


def _git_worktree(path):
    """The top folder of the git worktree that holds `path` (the nearest existing folder is asked), or None when it is in none."""
    probe = Path(path)
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    try:
        res = subprocess.run(["git", "-C", str(probe), "rev-parse", "--show-toplevel"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None                       # no git here: nothing to check against
    if res.returncode != 0:
        return None
    return res.stdout.strip() or None


def home(loops=1, fallback=None, worktrees=()) -> Path:
    """The SATVIK_HOME folder. More than one loop needs it set (HomeRequired otherwise); one loop may use `fallback` when it is unset.
    A home inside any git worktree is refused (git asks where the folder lives), as is a home inside any of `worktrees`."""
    raw = os.environ.get(ENV, "").strip()
    if not raw:
        if loops > 1:
            raise HomeRequired(f"{ENV} must be set when {loops} loops run: a folder outside every worktree")
        if fallback is None:
            raise HomeRequired(f"{ENV} is not set")
        raw = str(fallback)
    if not os.path.isabs(raw):
        raise LoopHomeError(f"{ENV} must be an absolute path, got {raw!r}")
    path = Path(os.path.realpath(raw))
    for wt in worktrees:
        if _inside(path, wt):
            raise LoopHomeError(f"the loop home {path} is inside the worktree {wt}")
    top = _git_worktree(path)
    if top:
        raise LoopHomeError(f"the loop home {path} is inside the git worktree {top}; a checkout or merge can wipe it. Use a folder outside every worktree")
    path.mkdir(parents=True, exist_ok=True)
    return path


def pid_alive(pid) -> bool:
    """Never os.kill on Windows: there, signal 0 terminates the process."""
    pid = int(pid or 0)
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.restype = ctypes.c_void_p
        k32.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
        k32.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
        k32.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = k32.OpenProcess(0x1000, False, pid)              # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return ctypes.get_last_error() == 5                     # access denied: it exists
        try:
            code = ctypes.c_ulong()
            if not k32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return True
            return code.value == 259                                # STILL_ACTIVE
        finally:
            k32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


_THREAD_LOCKS = {}                         # POSIX fcntl locks do not exclude threads of one process; one thread lock per lock file
_THREAD_LOCKS_GUARD = threading.Lock()


def _thread_lock(path):
    with _THREAD_LOCKS_GUARD:
        return _THREAD_LOCKS.setdefault(os.path.normcase(str(path)), threading.Lock())


@contextmanager
def _os_lock(path, timeout_s=LOCK_TIMEOUT_S):
    """One OS byte-range lock on `path` (plus a thread lock in this process), released when the block ends or the process dies.
    Never nest two calls on the same file: the second would wait for the first."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tlock = _thread_lock(path)
    if not tlock.acquire(timeout=timeout_s):
        raise LockTimeout(f"another thread of this process holds {path} for {timeout_s}s")
    try:
        fd = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0))
        try:
            end = time.monotonic() + timeout_s
            while True:
                try:
                    os.lseek(fd, LOCK_OFFSET, 0)
                    if msvcrt:
                        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                    else:
                        fcntl.lockf(fd, fcntl.LOCK_EX | fcntl.LOCK_NB, 1, LOCK_OFFSET, 0)
                    break
                except OSError:
                    if time.monotonic() >= end:
                        raise LockTimeout(f"could not lock {path} within {timeout_s}s")
                    time.sleep(0.02)
            try:
                yield
            finally:
                try:
                    os.lseek(fd, LOCK_OFFSET, 0)
                    if msvcrt:
                        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                    else:
                        fcntl.lockf(fd, fcntl.LOCK_UN, 1, LOCK_OFFSET, 0)
                except OSError:
                    pass                  # closing the handle drops the lock anyway
        finally:
            os.close(fd)
    finally:
        tlock.release()


def _locked(root, timeout_s=LOCK_TIMEOUT_S):
    """The single shared-state lock: home/lock."""
    return _os_lock(Path(root) / "lock", timeout_s)


def merge_lock(home_dir=None, timeout_s=LOCK_TIMEOUT_S):
    """home/merge.lock: one merge at a time across loops. A separate file from home/lock, so a merge never blocks state writes."""
    return _os_lock(_dir(home_dir) / "merge.lock", timeout_s)


def _dir(home_dir) -> Path:
    return home() if home_dir is None else Path(home_dir)


def _load(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except ValueError as exc:
        raise LoopHomeError(f"corrupt shared file {path}; not overwritten: {exc}") from exc


def _write_bytes(path, data: bytes):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_bytes(data)
    for _ in range(40):                   # Windows refuses a replace while another process has the file open for a moment
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            time.sleep(0.05)
    os.replace(tmp, path)


def _save(path, obj):
    _write_bytes(path, json.dumps(obj, indent=2, sort_keys=True).encode("utf-8"))


def _check_id(job_id):
    if not isinstance(job_id, str) or not JOB_ID_RE.match(job_id):
        raise ValueError(f"bad job id {job_id!r}")


def _lease_path(root, job_id) -> Path:
    _check_id(job_id)
    return Path(root) / "leases" / f"{job_id}.json"


def _is_stale(lease, now) -> bool:
    return (not lease.get("released") and now - float(lease.get("heartbeat", 0.0)) > STALE_S
            and not pid_alive(lease.get("pid", 0)))


def _live(root, job_id) -> bool:
    """A claim or a running request is held while its lease is not released and its process is alive. The 5-minute heartbeat rule governs
    only a lease TAKEOVER (Lease.acquire, Lease.stale), so a killed job frees its claims at once."""
    lease = _load(_lease_path(root, job_id), None)
    return lease is not None and not lease.get("released") and pid_alive(lease.get("pid", 0))


def _stored_gen(root, job_id) -> int:
    lease = _load(_lease_path(root, job_id), None)
    return int(lease["generation"]) if lease else 0


def _fence(root, job_id, gen):
    stored = _stored_gen(root, job_id)
    if gen < stored:
        raise Fenced(f"generation {gen} of {job_id} is older than the stored generation {stored}")


def new_job_id(name, home_dir=None) -> str:
    """<name>-<YYYYMMDD-HHMM>-<4 hex>. The id is reserved in the home under the lock, so two launches never get the same one."""
    root = _dir(home_dir)
    slug = re.sub(r"[^A-Za-z0-9]+", "-", str(name)).strip("-")[:40].strip("-") or "job"
    with _locked(root):
        while True:
            job_id = f"{slug}-{time.strftime('%Y%m%d-%H%M')}-{secrets.token_hex(2)}"
            if not _lease_path(root, job_id).exists():
                _save(_lease_path(root, job_id), {"job_id": job_id, "pid": 0, "started": 0.0, "heartbeat": 0.0, "generation": 0,
                                                  "released": True})
                return job_id


class Lease:
    def __init__(self, home_dir=None):
        self._home = home_dir

    def acquire(self, job_id) -> int:
        """Take the job's lease and return its new generation. A live holder raises LeaseHeld."""
        _check_id(job_id)
        root = _dir(self._home)
        with _locked(root):
            path = _lease_path(root, job_id)
            cur = _load(path, None)
            now = time.time()
            if cur is not None and not cur.get("released") and not _is_stale(cur, now):
                raise LeaseHeld(f"job {job_id} is held by pid {cur.get('pid')}")
            gen = (int(cur.get("generation", 0)) if cur else 0) + 1
            _save(path, {"job_id": job_id, "pid": os.getpid(), "started": now, "heartbeat": now, "generation": gen, "released": False})
            return gen

    def beat(self, job_id, gen):
        root = _dir(self._home)
        with _locked(root):
            path = _lease_path(root, job_id)
            cur = _load(path, None)
            if cur is None or int(cur.get("generation", 0)) != gen:
                raise LeaseLost(f"lease of {job_id} is gone or its generation {gen} changed")
            cur["heartbeat"] = time.time()
            _save(path, cur)

    def release(self, job_id, gen) -> bool:
        """False (and nothing changes) when `gen` is not the stored generation."""
        root = _dir(self._home)
        with _locked(root):
            path = _lease_path(root, job_id)
            cur = _load(path, None)
            if cur is None or int(cur.get("generation", 0)) != gen:
                return False
            cur["released"] = True
            _save(path, cur)
            return True

    def stale(self, job_id) -> bool:
        """Heartbeat older than STALE_S AND a dead pid. A missing or released lease is not stale."""
        root = _dir(self._home)
        with _locked(root):
            cur = _load(_lease_path(root, job_id), None)
            return cur is not None and not cur.get("released") and _is_stale(cur, time.time())

    def held(self, job_id) -> bool:
        """The claims' liveness rule: the lease is not released and its process is alive. A missing or released lease is not held."""
        root = _dir(self._home)
        with _locked(root):
            return _live(root, job_id)

    def keep_alive(self, job_id, gen, every_s=HEARTBEAT_S):
        """Beat in a daemon thread every `every_s` seconds. Returns the stop function; the thread also stops when the generation is fenced."""
        stop = threading.Event()

        def run():
            while not stop.wait(every_s):
                try:
                    self.beat(job_id, gen)
                except LoopHomeError:
                    return

        threading.Thread(target=run, daemon=True).start()
        return stop.set


def _rel(path) -> str:
    """A repo-relative POSIX key: no drive, no leading slash, no '..'."""
    s = str(path).replace("\\", "/")
    if s.startswith("/") or (len(s) > 1 and s[1] == ":"):
        raise ValueError(f"not repo-relative: {path}")
    parts = [p for p in s.split("/") if p not in ("", ".")]
    if not parts or ".." in parts:
        raise ValueError(f"not repo-relative: {path}")
    return "/".join(parts)


def _key(rel) -> str:
    return rel.casefold() if os.name == "nt" else rel


class Claims:
    def __init__(self, home_dir=None):
        self._home = home_dir

    def take(self, job_id, gen, rel_paths, reads=()):
        """All or nothing. Returns (True, []) or (False, [conflicting repo-relative paths]). A path is exclusive for writes; reads share with
        other reads. Claims of a job whose lease is not live are dropped first (crash recovery)."""
        writes = list(dict.fromkeys(_rel(p) for p in rel_paths))
        wkeys = {_key(w) for w in writes}
        reads = [r for r in dict.fromkeys(_rel(p) for p in reads) if _key(r) not in wkeys]
        root = _dir(self._home)
        with _locked(root):
            _fence(root, job_id, gen)
            path = Path(root) / "claims.json"
            table = _load(path, {"version": 1, "claims": {}})["claims"]
            seen = {}

            def live(job):
                if job not in seen:
                    seen[job] = job == job_id or _live(root, job)
                return seen[job]

            conflicts = []
            for rel in writes:
                conflicts += [rel for e in table.get(_key(rel), []) if e["job"] != job_id and live(e["job"])]
            for rel in reads:
                conflicts += [rel for e in table.get(_key(rel), []) if e["mode"] == "w" and e["job"] != job_id and live(e["job"])]
            if conflicts:
                return False, sorted(set(conflicts))
            for key in list(table):
                table[key] = [e for e in table[key] if live(e["job"])]
                if not table[key]:
                    del table[key]
            for rel, mode in [(w, "w") for w in writes] + [(r, "r") for r in reads]:
                kept = [e for e in table.get(_key(rel), []) if e["job"] != job_id]
                table[_key(rel)] = kept + [{"job": job_id, "gen": gen, "mode": mode, "path": rel}]
            _save(path, {"version": 1, "claims": table})
            return True, []

    def release(self, job_id, gen) -> bool:
        root = _dir(self._home)
        with _locked(root):
            if _stored_gen(root, job_id) != gen:
                return False
            path = Path(root) / "claims.json"
            table = _load(path, {"version": 1, "claims": {}})["claims"]
            for key in list(table):
                table[key] = [e for e in table[key] if e["job"] != job_id]
                if not table[key]:
                    del table[key]
            _save(path, {"version": 1, "claims": table})
            return True

    def is_live(self, rel_path) -> bool:
        """True when a job with a live lease holds any claim on this repo-relative path."""
        root = _dir(self._home)
        with _locked(root):
            table = _load(Path(root) / "claims.json", {"claims": {}})["claims"]
            return any(_live(root, e["job"]) for e in table.get(_key(_rel(rel_path)), []))


class Registry:
    def __init__(self, home_dir=None):
        self._home = home_dir

    def _path(self, root) -> Path:
        return Path(root) / "registry.json"

    def lookup(self, key):
        root = _dir(self._home)
        with _locked(root):
            entry = _load(self._path(root), {}).get(key)
            return dict(entry) if entry else None

    def _seq(self, table) -> int:
        return 1 + max((int(e.get("seq", 0)) for e in table.values()), default=0)

    def recent(self, n):
        """The last n records, newest first, as {key, job, state, started, finished}. `finished` is None while a request runs."""
        root = _dir(self._home)
        with _locked(root):
            table = _load(self._path(root), {})
        rows = [{"key": k, "job": e.get("job"), "state": e.get("state"), "started": e.get("started"), "finished": e.get("finished"),
                 "seq": int(e.get("seq", 0))} for k, e in table.items() if not k.startswith(COUNTER_PREFIX)]
        rows.sort(key=lambda r: (r["finished"] or r["started"] or 0.0, r["seq"]), reverse=True)
        return [{k: v for k, v in r.items() if k != "seq"} for r in rows[:max(0, int(n))]]

    def begin(self, key, job_id, gen, base) -> str:
        """Start a request. Returns "tests-only" when the same request (same key and base) already passed, else "run". A request that another
        live job is running raises RequestRunning. The state is "running" until record() writes the result."""
        root = _dir(self._home)
        with _locked(root):
            _fence(root, job_id, gen)
            table = _load(self._path(root), {})
            cur = table.get(key)
            now = time.time()
            if cur and cur.get("job") != job_id and cur.get("state") == "running" and _live(root, cur["job"]):
                raise RequestRunning(f"request {key} is running in job {cur['job']}")
            passed = bool(cur) and cur.get("base") == base and (cur.get("state") == "passed" or bool(cur.get("was_passed")))
            table[key] = {"job": job_id, "gen": gen, "state": "running", "base": base, "started": now, "finished": None,
                          "seq": self._seq(table), "result": cur.get("result") if passed and cur else None, "was_passed": passed}
            _save(self._path(root), table)
            return "tests-only" if passed else "run"

    def record(self, key, job_id, gen, state, base, result):
        root = _dir(self._home)
        with _locked(root):
            _fence(root, job_id, gen)
            table = _load(self._path(root), {})
            prev = table.get(key) or {}
            now = time.time()
            table[key] = {"job": job_id, "gen": gen, "state": state, "base": base, "result": result,
                          "started": prev.get("started", now), "finished": now, "seq": self._seq(table)}
            _save(self._path(root), table)

    def bump(self, name, by=1) -> int:
        """Add `by` to the named counter (kept in the registry under "counter:<name>", outside the request rows). Returns the new count."""
        root = _dir(self._home)
        with _locked(root):
            table = _load(self._path(root), {})
            key = COUNTER_PREFIX + name
            count = int((table.get(key) or {}).get("count", 0)) + int(by)
            table[key] = {"count": count}
            _save(self._path(root), table)
            return count

    def counters(self) -> dict:
        """Every counter as {name: count}."""
        root = _dir(self._home)
        with _locked(root):
            table = _load(self._path(root), {})
        return {k[len(COUNTER_PREFIX):]: int(e.get("count", 0)) for k, e in table.items() if k.startswith(COUNTER_PREFIX)}


class Gate:
    """Waits for a free slot named `name`. Holders whose pid is dead are pruned. WP4 adds pacing and the shared ledger."""

    def __init__(self, home_dir=None):
        self._home = home_dir

    @contextmanager
    def slot(self, name, gen, timeout_s, capacity=None):
        root = _dir(self._home)
        cap = capacity or CAPACITY.get(name, 1)
        token = secrets.token_hex(8)
        path = Path(root) / "gates.json"
        end = time.monotonic() + timeout_s
        waited = False
        while True:
            with _locked(root):
                table = _load(path, {})
                held = [e for e in table.get(name, []) if pid_alive(e.get("pid", 0))]
                if len(held) < cap:
                    held.append({"pid": os.getpid(), "gen": gen, "token": token})
                    table[name] = held
                    _save(path, table)
                    break
                table[name] = held
                _save(path, table)
            if time.monotonic() >= end:
                raise TimeoutError(f"no free slot for {name} within {timeout_s}s")
            if not waited:                                     # once per job that waits for a slot (a refusal at timeout 0 does not wait)
                waited = True
                Registry(self._home).bump("queue-wait")
            time.sleep(0.05)
        try:
            yield
        finally:
            with _locked(root):
                table = _load(path, {})
                table[name] = [e for e in table.get(name, []) if e.get("token") != token]
                _save(path, table)


def fenced_write(path, data, job_id, gen, home_dir=None):
    """Write `path` atomically unless `gen` is lower than the job's stored generation (Fenced). str, bytes or JSON-able data."""
    root = _dir(home_dir)
    with _locked(root):
        _fence(root, job_id, gen)
        if isinstance(data, bytes):
            payload = data
        elif isinstance(data, str):
            payload = data.encode("utf-8")
        else:
            payload = json.dumps(data, indent=2).encode("utf-8")
        _write_bytes(path, payload)


lease = Lease()
claims = Claims()
registry = Registry()
gate = Gate()
