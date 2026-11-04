"""I39 (2026-11-04): the merge sandbox can run the suite. Two rules, checked from the source, not from a list in the test:

1. Every third-party top-level import the suite executes (pieces/, scripts/, monitor/) is pinned in tools/sandbox/Dockerfile, and so is what
   fastapi.testclient needs (httpx). A pin satisfies the lower bound in monitor/requirements.txt.
2. Every repo folder the test code reads by path (a string literal naming a top-level folder, such as "monitor", "ui", "tools", "specs") is in
   merge_queue.SUITE_INCLUDE, the folders the merge check copies into the sandbox.

Both were missing when the merge check was written (the audit's I39: the full suite fails at merge). No Docker, no network: the real build and
the real run of default_suite are recorded in the commit message."""
import ast
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts" / "source"))
import merge_queue  # noqa: E402

DOCKERFILE = REPO / "tools" / "sandbox" / "Dockerfile"
REQUIREMENTS = REPO / "monitor" / "requirements.txt"
SUITE_CODE = sorted((REPO / "pieces").glob("*.py")) + sorted((REPO / "scripts" / "source").glob("*.py")) + sorted((REPO / "monitor").glob("*.py"))
# the import name -> the distribution the Dockerfile pins (only where they differ)
DISTRIBUTION = {"yaml": "pyyaml"}
# imported by the suite through another package: fastapi.testclient imports httpx, and monitor/api.py imports starlette directly
IMPLIED = {"fastapi.testclient": "httpx"}


def _trees():
    for path in SUITE_CODE:
        try:
            yield path, ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue


def _local_names():
    """Every module or package the repo itself provides: a .py stem or a folder name anywhere in scripts/, monitor/, pieces/, tests/, or at the
    repo root."""
    names = set()
    for folder in ("scripts", "monitor", "pieces", "tests"):
        names.update(p.stem for p in (REPO / folder).rglob("*.py"))
        names.update(p.parent.name for p in (REPO / folder).rglob("__init__.py"))
    names.update(p.name for p in REPO.iterdir() if p.is_dir() and not p.name.startswith("."))
    return names


def _pins():
    """{distribution: version} from the Dockerfile's pip install lines."""
    return {name.lower().replace("_", "-"): version
            for name, version in re.findall(r'"([A-Za-z0-9_.\-]+)==([A-Za-z0-9_.+\-]+)"', DOCKERFILE.read_text(encoding="utf-8"))}


def _third_party_imports():
    """{top-level name: {'module': the full dotted module}} for every import of the suite's code that is neither stdlib nor the repo's own."""
    stdlib, local = set(sys.stdlib_module_names), _local_names()
    found = {}
    for _path, tree in _trees():
        for node in ast.walk(tree):
            modules = []
            if isinstance(node, ast.Import):
                modules = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                modules = [node.module]
            for module in modules:
                top = module.split(".")[0]
                if top not in stdlib and top not in local:
                    found.setdefault(top, set()).add(module)
    return found


def _vt(text):
    return tuple(int(part) for part in re.findall(r"\d+", text))


def test_every_third_party_import_the_suite_runs_is_pinned_in_the_dockerfile():
    pins = _pins()
    imports = _third_party_imports()
    assert {"fastapi", "jsonschema", "pytest", "yaml", "starlette"} <= set(imports), sorted(imports)   # the scan sees the imports it must see
    missing = {}
    for top, modules in imports.items():
        distribution = DISTRIBUTION.get(top, top).lower().replace("_", "-")
        if distribution not in pins:
            missing[top] = sorted(modules)
    for module in {m for mods in imports.values() for m in mods}:
        for prefix, distribution in IMPLIED.items():
            if module == prefix or module.startswith(prefix + "."):
                if distribution not in pins:
                    missing[distribution] = [f"needed by {module}"]
    assert not missing, f"not pinned in tools/sandbox/Dockerfile: {missing}"


def test_the_pins_satisfy_the_lower_bounds_in_monitor_requirements():
    pins = _pins()
    bounds = {}
    for raw in REQUIREMENTS.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        match = re.fullmatch(r"([A-Za-z0-9_.\-]+)\s*>=\s*([\d.]+)", line)
        if match:
            bounds[match.group(1).lower().replace("_", "-")] = match.group(2)
    assert "fastapi" in bounds and "httpx" in bounds, bounds
    for name, bound in bounds.items():
        if name in ("uvicorn",):             # not imported by the suite: its pin is not needed in the test image
            continue
        assert name in pins, f"{name} is in monitor/requirements.txt and not pinned in the Dockerfile"
        assert _vt(pins[name]) >= _vt(bound), f"{name} pinned {pins[name]} is below monitor/requirements.txt's {bound}"


def test_suite_include_holds_every_folder_the_tests_read_by_path():
    """Every string literal in the test code that names a top-level folder of the repo (or specs/, which the guard tests glob) is a folder the
    suite reads, so it must be copied into the sandbox."""
    top = {p.name for p in REPO.iterdir() if p.is_dir() and not p.name.startswith(".") and p.name != "node_modules"} | {"specs"}
    read = set()
    for path in sorted((REPO / "pieces").glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value in top:
                read.add(node.value)
    include = set(merge_queue.SUITE_INCLUDE)
    assert {"monitor", "ui", "tools"} <= read, sorted(read)                 # the scan finds the folders named in the audit
    assert read - include == set(), f"the suite reads folders the merge copy does not include: {sorted(read - include)}"


def test_the_merge_copy_never_includes_secrets_or_loop_state():
    include = set(merge_queue.SUITE_INCLUDE)
    assert not include & {"secrets", "_loop", "_moss", ".git", ".env"}
