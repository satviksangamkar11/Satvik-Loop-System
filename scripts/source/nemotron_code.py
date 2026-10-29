"""Nemotron drafts code (owner rule 2026-10-27, docs/pilot/PIPELINE_README.md section 6).

  python scripts/source/nemotron_code.py --spec SPEC.md --out SCRATCH_DIR

The spec says what to write and names the tests it must pass. Nemotron (free NVIDIA pool) replies with files; this tool writes ONLY those that pass the path
rules into SCRATCH_DIR. Claude then reads every line, runs the tests and moves the files into the repository: nothing is written into the repository here.
Refused: paths outside the scratch folder, secrets, Blender/animation code (D-017), anything but .py/.json/.md. API keys are read from the usual place at run time."""
from __future__ import annotations

import argparse
import importlib.util
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODEL = "nvidia/nemotron-3-ultra-550b-a55b"
ALLOWED = {".py", ".json", ".md"}
FORBIDDEN_PART = {"secrets", ".git", ".claude", ".codex"}
FORBIDDEN_TEXT = re.compile(r"\bimport\s+bpy\b|\bfrom\s+bpy\b|\bbpy\.|\bbmesh\b|\bmathutils\b|blender", re.I)       # no model writes Blender/animation code
SYSTEM = ("You write small, plain Python 3.14 modules for the GranthStudio pipeline. Follow the spec exactly and make the given tests pass; the tests are frozen and you must not change them. Use only the standard library plus pyyaml and jsonschema. "
          "No network, no file writes outside what the spec names. Never access secrets/. Never edit the owner's EASY script or any existing Adhyay 1 artifact. Never write animation or Blender code. "
          "Rules of the pipeline: Kimi outputs contain no timings, dialogue or claims; Stage 2 selects existing catalog image ids only; Stage 1 effort is HIGH and Stage 2 LOW; tests use synthetic data and tmp files only. "
          "The spec lists every schema or API it relies on; do not assume anything it does not state. Reply with files only, each as:\nFILE: relative/path.py\n```python\n<complete file>\n```\n"
          "No other prose.")
BLOCK = re.compile(r"^FILE:\s*(\S+)\s*\n```[a-zA-Z]*\n(.*?)\n```", re.S | re.M)


def check_path(rel: str) -> str | None:
    """None if the path is acceptable inside the scratch folder, else the reason."""
    p = Path(rel)
    if p.is_absolute() or rel.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", rel):
        return "absolute path"
    if ".." in p.parts:
        return "parent folder"
    if FORBIDDEN_PART & {x.lower() for x in p.parts}:
        return "forbidden folder"
    if p.suffix.lower() not in ALLOWED:
        return f"extension {p.suffix!r} not allowed"
    return None


def parse_files(reply: str) -> tuple[dict, list]:
    """({relative path: text} accepted, [(path, reason)] refused)."""
    ok, refused = {}, []
    for rel, body in BLOCK.findall(reply or ""):
        why = check_path(rel) or ("Blender/animation code (D-017)" if FORBIDDEN_TEXT.search(body) else None)
        if why:
            refused.append((rel, why))
        elif rel in ok:
            refused.append((rel, "duplicate"))
        else:
            ok[rel] = body.rstrip("\n") + "\n"
    return ok, refused


def check_spec(path: Path) -> str | None:
    """None if the spec file may be sent to the model: a small .md/.txt file that is not under a forbidden folder."""
    p = Path(path).resolve()
    if FORBIDDEN_PART & {x.lower() for x in p.parts}:
        return "spec under a forbidden folder"
    if p.suffix.lower() not in {".md", ".txt"}:
        return "spec must be a .md or .txt file"
    if not p.is_file() or p.stat().st_size > 200_000:
        return "spec missing or larger than 200 KB"
    return None


def check_out(path: Path, roots: list) -> str | None:
    """None if the scratch folder is NEW and lies inside one of the allowed roots, but not inside the repository's tracked source folders."""
    p = Path(path).resolve()
    if p.exists():
        return "scratch folder exists"
    if FORBIDDEN_PART & {x.lower() for x in p.parts}:
        return "scratch folder under a forbidden folder"
    if not any(Path(r).resolve() in p.parents for r in roots):
        return "scratch folder outside the allowed roots"
    return None


def exact_set(files: dict, refused: list, expected: list) -> str | None:
    """None only if the reply holds exactly the expected files and nothing was refused (a partial or surprising reply is never written)."""
    if refused:
        return f"{len(refused)} block(s) refused"
    if set(files) != set(expected):
        return f"reply has {sorted(files)}, expected {sorted(expected)}"
    return None


def write_scratch(files: dict, out: Path) -> list[Path]:
    out = out.resolve()
    written = []
    for rel, text in files.items():
        dest = (out / rel).resolve()
        if out not in dest.parents:
            raise ValueError(f"{rel} escapes the scratch folder")
        if dest.exists():
            raise FileExistsError(f"{dest} exists; draft into a new folder")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text, encoding="utf-8")
        written.append(dest)
    return written


def _ds():
    spec = importlib.util.spec_from_file_location("ds", ROOT / "scripts" / "source" / "draft_source.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("ds", mod)
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--spec", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path, help="NEW scratch folder (must not exist)")
    ap.add_argument("--expect", required=True, help="comma-separated relative paths the reply must contain, exactly")
    ap.add_argument("--allow-root", action="append", default=[], help="folder the scratch folder may live in (default: the system temp folder)")
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--max-tokens", type=int, default=20000)
    a = ap.parse_args()
    import tempfile
    why = check_spec(a.spec) or check_out(a.out, a.allow_root or [tempfile.gettempdir()])
    if why:
        raise SystemExit(f"refused before any call: {why}")
    expected = [x.strip() for x in a.expect.split(",") if x.strip()]
    ds = _ds()
    nt = ds.nt
    keys = nt.load_keys()
    pool = nt.KeyPool(keys)
    reply, info = ds.chat_stream(pool, keys, a.model, SYSTEM, a.spec.read_text(encoding="utf-8"), max_tokens=a.max_tokens)
    reply = nt.scrub(reply or "", keys)
    print({k: info.get(k) for k in ("status", "wall_s", "finish", "error")})
    files, refused = parse_files(reply)
    for rel, why in refused:
        print("REFUSED", rel, why)
    why = exact_set(files, refused, expected)
    if why:
        a.out.mkdir(parents=True)
        (a.out / "RAW_REPLY.txt").write_text(reply, encoding="utf-8")
        print("nothing drafted:", why, "| raw reply saved in", a.out)
        return 1
    for p in write_scratch(files, a.out):
        print("drafted", p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
