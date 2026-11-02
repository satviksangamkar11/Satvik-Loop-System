"""WP3 test helpers (Revision 4): an in-memory stand-in for the WP2 interface (home, lease, claims, registry, gate) and temp git repos.

Nothing here calls a model. Git runs locally on temp repos. WP2 is not imported; when its module is merged, the tests can switch to it."""
import contextlib
import subprocess
from pathlib import Path


class LeaseHeld(Exception):
    pass


class LeaseLost(Exception):
    pass


class FakeLease:
    def __init__(self):
        self.gen = 0
        self.held = {}  # key -> generation

    def acquire(self, key):
        if key in self.held:
            raise LeaseHeld(key)
        self.gen += 1
        self.held[key] = self.gen
        return self.gen

    def beat(self, key, gen):
        if self.held.get(key) != gen:
            raise LeaseLost(key)

    def release(self, key, gen):
        if self.held.get(key) == gen:
            del self.held[key]

    def stale(self, key):
        return False


class FakeClaims:
    def __init__(self):
        self.owner = {}  # rel path -> job_id

    def take(self, job_id, gen, rel_paths):
        conflicts = sorted(p for p in rel_paths if self.owner.get(p, job_id) != job_id)
        if conflicts:
            return False, conflicts
        for p in rel_paths:
            self.owner[p] = job_id
        return True, []

    def release(self, job_id, gen):
        for p in [p for p, j in self.owner.items() if j == job_id]:
            del self.owner[p]


class FakeRegistry:
    def __init__(self):
        self.rows = {}

    def lookup(self, key):
        return self.rows.get(key)

    def record(self, key, job_id, gen, state, base, result):
        self.rows[key] = {"job_id": job_id, "gen": gen, "state": state, "base": base, "result": result}


class FakeGate:
    def __init__(self):
        self.busy = set()

    @contextlib.contextmanager
    def slot(self, name, gen, timeout_s):
        if name in self.busy:
            raise TimeoutError(name)
        yield


class FakeApi:
    def __init__(self, home_dir):
        self.home_dir = Path(home_dir)
        self.lease = FakeLease()
        self.claims = FakeClaims()
        self.registry = FakeRegistry()
        self.gate = FakeGate()

    def home(self):
        return self.home_dir


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True).stdout.strip()


def _identify(path):
    git(path, "config", "user.name", "wp3-test")
    git(path, "config", "user.email", "wp3-test@localhost")


def make_repos(root):
    """A bare origin whose main has one file, and a clone `repo` of it. Returns (origin, repo)."""
    root = Path(root)
    origin, seed, repo = root / "origin.git", root / "seed", root / "repo"
    git(root, "init", "-q", "--bare", "-b", "main", str(origin))
    git(root, "clone", "-q", str(origin), str(seed))
    _identify(seed)
    (seed / "base.txt").write_text("base\n", encoding="utf-8")
    git(seed, "add", "base.txt")
    git(seed, "commit", "-q", "-m", "base")
    git(seed, "push", "-q", "origin", "HEAD:main")
    git(root, "clone", "-q", str(origin), str(repo))
    _identify(repo)
    return origin, repo


def commit_in(wt, rel, text, msg="work"):
    (Path(wt) / rel).write_text(text, encoding="utf-8")
    git(wt, "add", rel)
    git(wt, "commit", "-q", "-m", msg)
    return git(wt, "rev-parse", "HEAD")


def push_to_main(origin, root, name, text):
    """Another writer lands a commit on origin main (stands in for someone else's merge)."""
    work = Path(root) / f"writer-{name}"
    git(root, "clone", "-q", str(origin), str(work))
    _identify(work)
    commit_in(work, f"{name}.txt", text, msg=f"other: {name}")
    git(work, "push", "-q", "origin", "HEAD:main")
    return git(work, "rev-parse", "HEAD")
