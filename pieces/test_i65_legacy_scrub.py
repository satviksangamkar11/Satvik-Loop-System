"""I65: the legacy (non-production) run_job scrubs with the loaded key VALUES. A {slot: key} dict contributes its values, never its slot names, and
the scrub is redact.scrub, which also replaces credential shapes. A coder reply that echoes a loaded key leaves no copy of the key in the job's
folder. No model call (the caller is a scripted fake)."""
import json
from pathlib import Path

import fix_loop as fl
from test_p0_review_gate import _legacy_job, _reviewer, _reply, F, R, make_root

KEY = "sk-or-v1-LEGACYKEY0123456789abcdef"


def _echoing_reply():
    return "# note: the key in use is " + KEY + "\n" + _reply()


def _run_with(tmp_path, scrub_keys):
    root = make_root(tmp_path)
    job = _legacy_job(root, ["lightning", "codex", "minimax"])
    panel = {"lightning": _reviewer(R), "codex": _reviewer(F), "minimax": _reviewer(R)}
    fl.run_job(job, root, caller=lambda s, u: (_echoing_reply(), {"status": 200}), reviewer=panel,
               pytest_runner=lambda *a, **k: (True, "ok"), scrub_keys=scrub_keys,
               callers={"judge": lambda s, u: ("## Verdict: READY\n", {"status": 200})})
    return root


def _texts_under(folder):
    for path in Path(folder).rglob("*"):
        if path.is_file():
            try:
                yield path, path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue


def test_a_slot_keyed_dict_scrubs_the_key_values_from_the_job_folder(tmp_path):
    root = _run_with(tmp_path, {"free": KEY})
    leaked = [str(p) for p, text in _texts_under(root / "_loop") if KEY in text]
    assert leaked == []


def test_a_list_of_key_values_scrubs_the_same_way(tmp_path):
    root = _run_with(tmp_path, [KEY])
    assert [str(p) for p, text in _texts_under(root / "_loop") if KEY in text] == []


def test_the_slot_name_is_not_used_as_the_scrub_value(tmp_path):
    """Scrubbing with the slot name would replace the word 'free' and leave the key in place: the normalised list holds the value only."""
    import redact
    assert redact.values_of({"free": KEY}) == [KEY]
    assert "free" not in redact.values_of({"free": KEY})
