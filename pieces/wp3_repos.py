"""WP3 test helpers (Revision 4): temp git repos (a bare origin and a clone) and reads of WP2's lease files.

Nothing here calls a model. The WP2 objects themselves (loop_home) are used as they are, with home_dir set to a temp folder."""
import json
import subprocess
from pathlib import Path


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


def lease_held(home, key):
    """True when home/leases/<key>.json exists and is not released (WP2 lease file)."""
    path = Path(home) / "leases" / f"{key}.json"
    return path.exists() and not json.loads(path.read_text(encoding="utf-8")).get("released")


def live_leases(home):
    """Job ids whose WP2 lease is not released."""
    folder = Path(home) / "leases"
    if not folder.exists():
        return []
    return sorted(p.stem for p in folder.glob("*.json") if not json.loads(p.read_text(encoding="utf-8")).get("released"))
