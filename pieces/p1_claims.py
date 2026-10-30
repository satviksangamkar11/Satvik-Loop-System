"""Safe parallel loops (decision P1-D6/D10, spec specs/phase1_claims.md): shared key ledger, dead-key markers, file claims, job launcher.
No network, no model call. Keys are never stored: only sha256(key)[:16] hashes reach the shared files."""
import hashlib
import uuid
import json
import os
import runpy
import sys
import time
from pathlib import Path

try:
    import msvcrt
except ImportError:                      # not Windows: tests still run with fcntl
    msvcrt = None
    import fcntl

LOCK_OFFSET = 1 << 30                    # lock byte far beyond any content: Windows blocks other handles from locked ranges
DEAD_STATUSES = (401, 402, 403)


class LedgerError(RuntimeError):
    pass


class LedgerLockTimeout(LedgerError):
    pass


class AllKeysUnavailable(RuntimeError):
    pass


class ClaimConflict(RuntimeError):
    pass


def key_hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def canonical_path(path) -> str:
    return os.path.normcase(os.path.realpath(os.path.abspath(str(path))))


class FileLock:
    """Persistent lock file, never deleted; the OS drops the lock when the process dies."""

    def __init__(self, path):
        self.path = str(path)
        self._fd = None

    def try_acquire(self) -> bool:
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0))
        try:
            os.lseek(fd, LOCK_OFFSET, 0)
            if msvcrt:
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                fcntl.lockf(fd, fcntl.LOCK_EX | fcntl.LOCK_NB, 1, LOCK_OFFSET, 0)
        except OSError:
            os.close(fd)
            return False
        self._fd = fd
        return True

    def _try_or_raise(self) -> bool:
        try:
            return self.try_acquire()
        except OSError as exc:
            raise LedgerError(f"cannot open lock file {self.path}: {exc}") from exc

    def acquire(self, timeout_s: float = 30.0, poll_s: float = 0.05):
        end = time.monotonic() + timeout_s
        while not self._try_or_raise():
            if time.monotonic() >= end:
                raise LedgerLockTimeout(f"could not lock {self.path} within {timeout_s}s")
            time.sleep(poll_s)
        return self

    def write_info(self, text: str):
        if self._fd is not None:
            try:
                os.lseek(self._fd, 0, 0)
                os.ftruncate(self._fd, 0)
                os.write(self._fd, text.encode("utf-8"))
            except OSError:
                pass                      # information only

    def release(self):
        if self._fd is None:
            return
        fd, self._fd = self._fd, None
        try:
            os.lseek(fd, LOCK_OFFSET, 0)
            if msvcrt:
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                fcntl.lockf(fd, fcntl.LOCK_UN, 1, LOCK_OFFSET, 0)
        except OSError:
            pass
        finally:
            os.close(fd)

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *exc):
        self.release()


def _write_json_default(path, obj):
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh)
    os.replace(tmp, path)


def _make_marker_default(path):
    try:
        os.close(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
    except FileExistsError:
        pass


class KeyLedger:
    def __init__(self, directory, per_key=10, window_s=60.0, cool_s=60.0, dead_cool_s=86400.0, offset=0,
                 clock=time.time, write_json=None, make_marker=None, exit_fn=os._exit, lock_timeout_s=30.0):
        self.dir = str(directory)
        if not os.path.isdir(self.dir):
            raise ValueError(f"ledger folder does not exist: {self.dir}")
        self.per_key, self.window_s, self.cool_s, self.dead_cool_s = per_key, window_s, cool_s, dead_cool_s
        self.offset, self.clock, self.exit_fn, self.lock_timeout_s = offset, clock, exit_fn, lock_timeout_s
        self._write_json = write_json or _write_json_default
        self._make_marker = make_marker or _make_marker_default
        self._ledger_path = os.path.join(self.dir, "key_ledger.json")
        self._lock = FileLock(os.path.join(self.dir, "key_ledger.lock"))

    # ---- state helpers (always called while the lock is held)
    def _read(self, hashes, now):
        try:
            with open(self._ledger_path, encoding="utf-8") as fh:
                data = json.load(fh)["keys"]
            if not isinstance(data, dict):
                raise ValueError("keys is not an object")
            for entry in data.values():                     # every entry must be {"stamps": [numbers], "cool_until": number}
                ok = (isinstance(entry, dict) and isinstance(entry.get("stamps", []), list)
                      and all(isinstance(t, (int, float)) for t in entry.get("stamps", []))
                      and isinstance(entry.get("cool_until", 0.0), (int, float)))
                if not ok:
                    raise ValueError("malformed ledger entry")
            return data, False
        except FileNotFoundError:
            return {}, False
        except (ValueError, KeyError, TypeError, OSError):          # unparseable: rebuild with every key cooled
            return {h: {"stamps": [], "cool_until": now + self.cool_s} for h in hashes}, True

    def _save(self, data):
        try:
            self._write_json(self._ledger_path, {"version": 1, "keys": data})
        except Exception as exc:
            raise LedgerError(f"write ledger failed: {exc}") from exc

    def dead_hashes(self) -> set:
        try:
            return {n[5:-5] for n in os.listdir(self.dir) if n.startswith("dead_") and n.endswith(".mark")}
        except OSError as exc:
            raise LedgerError(f"cannot list dead markers: {exc}") from exc

    # ---- public
    def choose(self, hashes):
        self._lock.acquire(self.lock_timeout_s)
        try:
            now = self.clock()
            data, rebuilt = self._read(hashes, now)
            dead = self.dead_hashes()
            for h in hashes:
                entry = data.setdefault(h, {"stamps": [], "cool_until": 0.0})
                entry["stamps"] = [t for t in entry.get("stamps", []) if now - t < self.window_s]
                entry.setdefault("cool_until", 0.0)
            live = [i for i, h in enumerate(hashes) if h not in dead]
            if not live:
                raise AllKeysUnavailable("every key has a dead marker")
            n = len(hashes)
            order = [(self.offset + k) % n for k in range(n)]
            ready = [i for i in order if hashes[i] not in dead and data[hashes[i]]["cool_until"] <= now
                     and len(data[hashes[i]]["stamps"]) < self.per_key]
            if ready:
                pick = min(ready, key=lambda i: len(data[hashes[i]]["stamps"]))      # stable: ties follow the rotated order
                data[hashes[pick]]["stamps"].append(now)
                self._save(data)                                                      # persisted BEFORE the request is sent
                return pick
            if rebuilt:
                self._save(data)
            waits = []
            for i in live:
                e = data[hashes[i]]
                need = [e["cool_until"] - now] if e["cool_until"] > now else []
                if len(e["stamps"]) >= self.per_key:
                    need.append(min(e["stamps"]) + self.window_s - now)
                waits.append(max(need) if need else 0.0)
            return max(min(waits), 0.05)
        finally:
            self._lock.release()

    def report(self, h, status):
        if 200 <= status < 300:
            return
        self._lock.acquire(self.lock_timeout_s)
        try:
            now = self.clock()
            data, rebuilt = self._read([h], now)
            if rebuilt:                                   # corrupt ledger: never rewrite it with one key only; choose() rebuilds it with EVERY key cooled
                if status in DEAD_STATUSES:
                    try:
                        self._make_marker(os.path.join(self.dir, f"dead_{h}.mark"))
                    except Exception as exc:
                        raise LedgerError(f"dead marker failed on a corrupt ledger: {exc}") from exc
                return
            if h not in data:
                return                                    # unknown key hash: ignored (a real key was stamped by choose() first)
            entry = data.setdefault(h, {"stamps": [], "cool_until": 0.0})
            if status in DEAD_STATUSES:
                entry["cool_until"] = max(entry.get("cool_until", 0.0), now + self.dead_cool_s)
                saved = marked = False
                try:
                    self._save(data)
                    saved = True
                except Exception:
                    pass
                try:
                    self._make_marker(os.path.join(self.dir, f"dead_{h}.mark"))
                    marked = True
                except Exception:
                    pass
                if not saved and not marked:
                    self.exit_fn(2)                      # real: the process ends and Windows frees the lock
                    raise LedgerError("both writes failed")
                if not (saved and marked):
                    raise LedgerError("one of the two dead-key writes failed")
            else:
                entry["cool_until"] = max(entry.get("cool_until", 0.0), now + self.cool_s)
                self._save(data)
        finally:
            self._lock.release()


class LedgerKeyPool:
    """Drop-in for the loop's KeyPool: keys, acquire() -> (index, key), report(index, status), live."""

    def __init__(self, keys, directory, offset=None, per_key=10, clock=time.time, sleep=time.sleep, **ledger_options):
        self.keys = list(keys)
        self._hashes = [key_hash(k) for k in self.keys]
        if offset is None:
            offset = os.getpid() % max(len(self.keys), 1)
        self._ledger = KeyLedger(directory, per_key=per_key, offset=offset, clock=clock, **ledger_options)
        self._sleep = sleep
        self._poisoned = False
        self._dead = set()

    def acquire(self):
        if self._poisoned:
            raise LedgerError("poisoned")
        while True:
            try:
                result = self._ledger.choose(self._hashes)
            except LedgerError:
                self._poisoned = True
                raise
            if isinstance(result, int):
                return result, self.keys[result]
            self._sleep(result)

    def report(self, index, status):
        if not 0 <= index < len(self.keys):
            return
        try:
            self._ledger.report(self._hashes[index], status)
        except LedgerError:
            self._poisoned = True
            raise
        if status in DEAD_STATUSES:
            self._dead.add(index)

    @property
    def live(self) -> int:
        dead = self._ledger.dead_hashes()
        return sum(1 for i, h in enumerate(self._hashes) if h not in dead and i not in self._dead)


class Claim:
    def __init__(self, locks, files=()):
        self._locks = locks
        self._files = list(files)             # reader lock files of this claim: removed again on release

    def release(self):
        locks, self._locks = self._locks, []
        for lock in locks:
            lock.release()
        files, self._files = self._files, []
        for name in files:
            try:
                os.unlink(name)
            except OSError:
                pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.release()


class ClaimStore:
    def __init__(self, directory):
        self.dir = str(directory)
        if not os.path.isdir(self.dir):
            raise ValueError(f"claims folder does not exist: {self.dir}")

    def _hash(self, canon: str) -> str:
        return hashlib.sha256(canon.encode()).hexdigest()[:16]

    def _live_reader(self, canon: str) -> bool:
        """True when another job still reads this file. A reader holds an OS lock on `read_<hash>_<pid>.lock` for its whole life; a lock that can be
        taken belongs to a dead reader and is removed (F10)."""
        prefix = f"read_{self._hash(canon)}_"
        for name in os.listdir(self.dir):
            if not (name.startswith(prefix) and name.endswith(".lock")):
                continue
            probe = FileLock(os.path.join(self.dir, name))
            try:
                free = probe.try_acquire()
            except OSError:
                free = False
            if free:
                probe.release()
                try:
                    os.unlink(os.path.join(self.dir, name))
                except OSError:
                    pass
            else:
                return True
        return False

    def claim(self, paths, job_name="", reads=()) -> Claim:
        """`paths` are claimed exclusively (a job writes them). `reads` are files the job only READS (reference files, the spec): any number of readers
        may hold one together, but a reader and a writer of the same file exclude each other, so nobody ever reads a file that is being rewritten."""
        taken, seen, reader_files = [], set(), []

        def give_up(canon):
            for held in taken:
                held.release()
            for name in reader_files:
                try:
                    os.unlink(name)
                except OSError:
                    pass
            raise ClaimConflict(canon)

        for path in paths:
            canon = canonical_path(path)
            if canon in seen:
                continue                                  # a job lists its test file under files AND tests: duplicates in ONE call are normal
            seen.add(canon)
            lock = FileLock(os.path.join(self.dir, f"claim_{self._hash(canon)}.json"))
            try:
                ok = lock.try_acquire()
            except OSError:
                ok = False
            if not ok:
                give_up(canon)
            if self._live_reader(canon):
                lock.release()
                give_up(canon + " (being read by another job)")
            lock.write_info(json.dumps({"path": canon, "job": job_name, "pid": os.getpid(), "started": time.time()}))
            taken.append(lock)
        for path in reads:
            canon = canonical_path(path)
            if canon in seen:
                continue                                  # this job writes it itself
            seen.add(canon)
            gate = FileLock(os.path.join(self.dir, f"claim_{self._hash(canon)}.json"))
            try:
                ok = gate.try_acquire()                   # momentary: no writer may hold the file while a reader registers
            except OSError:
                ok = False
            if not ok:
                give_up(canon + " (being written by another job)")
            try:
                reader_name = os.path.join(self.dir, f"read_{self._hash(canon)}_{os.getpid()}_{uuid.uuid4().hex[:8]}.lock")
                mark = FileLock(reader_name)
                if not mark.try_acquire():
                    give_up(canon + " (reader mark busy)")
                taken.append(mark)
                reader_files.append(reader_name)
            finally:
                gate.release()
        return Claim(taken, reader_files)


def reconcile_stale(repo, claims_dir=None, note="") -> list:
    """Honest cleanup of jobs killed while running. A job folder whose status.json says `running` but whose claim on `_loop/<job>` can be taken has no live
    process: its state becomes `stopped` (with the time, the last round and the note), NEVER passed. Returns the job names that were changed."""
    repo = Path(repo)
    claims = Path(claims_dir or repo / "_loop")
    changed = []
    loop_dir = repo / "_loop"
    if not loop_dir.is_dir():
        return changed
    for folder in sorted(p for p in loop_dir.iterdir() if p.is_dir() and p.name != "jobs"):
        status_file = folder / "status.json"
        try:
            status = json.loads(status_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(status, dict) or status.get("state") != "running":
            continue
        probe = FileLock(claims / f"claim_{hashlib.sha256(canonical_path(folder).encode()).hexdigest()[:16]}.json")
        try:
            free = probe.try_acquire()
        except OSError:
            free = False
        if not free:
            continue                                      # a live process still holds the job
        try:
            status["state"] = "stopped"
            status["stopped_reason"] = "process gone while the status said running" + (f"; {note}" if note else "")
            status["stopped_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            tmp = status_file.with_name("status.json.tmp")
            tmp.write_text(json.dumps(status, indent=2), encoding="utf-8")
            os.replace(tmp, status_file)
            changed.append(folder.name)
        finally:
            probe.release()
    return changed


def _after_job(job, job_path, repo, code, escalate, loop_log):
    """Inside the loop (P1-D26): a job that escalated (code 3) is diagnosed by Sonnet 5.5 medium (read-only); every result is appended to docs/LOOP_LOG.md."""
    repo = Path(repo)
    diagnosis = None
    if code == 3:
        try:
            if escalate is None:
                sys.path.insert(0, str(Path(__file__).resolve().parent))
                import p1_escalate
                escalate = p1_escalate.diagnose
            diagnosis = escalate(job_path, repo)
        except Exception:
            diagnosis = None
    if not loop_log or not (repo / "docs").is_dir():
        return
    state, verdicts = "", []
    try:
        status = json.loads((repo / "_loop" / str(job.get("name", "")) / "status.json").read_text(encoding="utf-8"))
        state = status.get("state", "")
        verdicts = [r.get("reason", "") for r in status.get("rounds", []) if str(r.get("round", "")).startswith("review")]
    except (OSError, ValueError):
        pass
    line = (f"- {time.strftime('%Y-%m-%d %H:%M')} job {job.get('name')}: exit {code}, state {state or 'unknown'}"
            + (f", reviews {verdicts}" if verdicts else "") + (f", diagnosis {Path(diagnosis).name}" if diagnosis else "") + "\n")
    try:
        with open(repo / "docs" / "LOOP_LOG.md", "a", encoding="utf-8") as fh:
            fh.write(line)
    except OSError:
        pass


def run_job(job_path, repo, claims_dir=None, loop_script=None, environ=None, escalate=None, loop_log=True) -> int:
    repo = Path(repo)
    job_path = Path(job_path)
    if not job_path.is_absolute():
        job_path = repo / job_path
    job = json.loads(job_path.read_text(encoding="utf-8"))
    fixers = job.get("fixers")
    if not isinstance(fixers, list) or not fixers or any(f not in ("nemotron", "haiku") for f in fixers):
        raise ValueError("job fixers must be nemotron or haiku (owner exception 2026-10-31)")
    paths = [job_path, repo / "_loop" / str(job.get("name", ""))] + [repo / f for f in list(job.get("files", [])) + list(job.get("tests", []))]
    env = os.environ if environ is None else environ
    script = str(loop_script or repo / "scripts" / "source" / "fix_loop.py")
    reads = ([repo / str(r) for r in list(job.get("reference") or []) + list(job.get("regression") or [])]
             + [repo / str(job.get("spec", ""))]
             + ([repo / str(job.get("review_prompt"))] if job.get("review_prompt") else [])
             + [repo / str(r) for r in list(job.get("review_files") or [])])
    (repo / "_loop").mkdir(exist_ok=True)                    # the loop's own state folder (a fresh repo has none)
    claim = ClaimStore(claims_dir or repo / "_loop").claim(paths, job.get("name", ""), reads=reads)
    old_argv = sys.argv
    saved_env = {k: env.get(k) for k in ("PYTEST_ADDOPTS", "P1_LEDGER", "P1_KEY_OFFSET")}      # restored in finally: the launcher must not leak into its caller (P1-D21)
    try:
        opts = env.get("PYTEST_ADDOPTS", "")
        if "-p no:cacheprovider" not in opts:
            env["PYTEST_ADDOPTS"] = (opts + " -p no:cacheprovider").strip()
        env["P1_LEDGER"] = "1"
        env["P1_KEY_OFFSET"] = str(os.getpid() % 14)
        sys.argv = [script, "--job", str(job_path), "--root", str(repo)]
        try:
            runpy.run_path(script, run_name="__main__")
            code = 0
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
        except Exception:                      # the loop crashed (OSError, reviewer build error ...): still log it and release the claims (P1-D36)
            import traceback
            traceback.print_exc()              # never invisible: the log of the run shows why
            code = 1
        _after_job(job, job_path, repo, code, escalate, loop_log)
        return code
    finally:
        sys.argv = old_argv
        for k, v in saved_env.items():
            if v is None:
                env.pop(k, None)
            else:
                env[k] = v
        claim.release()


def main(argv) -> int:
    if len(argv) < 2 or argv[0] != "run":
        print("usage: p1_claims.py run <job.json> [repo]")
        return 2
    repo = argv[2] if len(argv) > 2 else os.getcwd()
    try:
        reconcile_stale(repo)                         # jobs killed earlier must not stay `running` (A2)
        return run_job(argv[1], repo)
    except ClaimConflict as exc:
        print(f"claims: conflict {exc}")
        return 4                                      # the loop itself uses 3 for escalate


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
