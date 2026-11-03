"""R2 repairs (2026-11-03): SEARCH/REPLACE edits for writer repairs (one re-ask with the example, then the whole-file request), the repair digest
(assertion line, stdout, stderr, test source), raw reply and reasoning files linked from the call record, FILE: header variants and fenced bodies,
the repair token cap with no bigger retry, and the writer example when reasoning is off. Fakes only: no network, no model call, no Docker."""
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts" / "source"))
sys.path.insert(0, str(REPO / "pieces"))
import nemotron_code as nc  # noqa: E402
import openrouter_call as oc  # noqa: E402
import p1b_production as pm  # noqa: E402
import prod_harness as ph  # noqa: E402
from test_openrouter_call import Fake, client, ok, WRITER_OK  # noqa: E402
from test_p1b_production_run import FakeClient, FakeSandbox, PromptClient, load, p1b_job, wp_h_root  # noqa: E402

FILES = ["pieces/calc.py", "pieces/test_calc.py"]
CALC_BEFORE = "def calculate_total(prices):\n    return 0\n"        # what wp_h_root writes into pieces/calc.py
CALC_AFTER = "def calculate_total(prices):\n    return 50\n"


@pytest.fixture(autouse=True)
def _production_roster(profile):
    profile("production")


def dots():
    return oc.roster_state()["ROLES"]["writer_a"]


def block(rel, search, replace):
    """One FILE: section with one SEARCH/REPLACE block."""
    return f"FILE: {rel}\n<<<<<<< SEARCH\n{search}=======\n{replace}>>>>>>> REPLACE\n"


def production(root, client_, scrub_keys=()):
    job = load(root, p1b_job())
    history = root / "_loop" / "calc" / "history"
    history.mkdir(parents=True, exist_ok=True)
    models = pm.Production(job, root, client_, FakeSandbox({}), protected=[], acceptance_lock=None, originals={},
                           parse_files=nc.parse_files, check_path=nc.check_path, content_problem=lambda rel, content: None,
                           history=history, job_dir=root / "_loop" / "calc", scrub_keys=scrub_keys)
    return models, history


# ---------------------------------------------------------------- 2. FILE: headers and fenced bodies

def test_the_three_file_headers_parse_with_fenced_and_raw_bodies():
    reply = "FILE: a.py\n```python\nX = 1\n```\n### FILE: b.py\n```\nY = 2\n```\nFILE: c.py\nZ = 3\n"
    parsed, refused = nc.parse_files(reply)
    assert refused == []
    assert parsed == {"a.py": "X = 1\n", "b.py": "Y = 2\n", "c.py": "Z = 3\n"}


def test_a_bold_file_header_is_accepted_by_the_parser_and_the_reply_check():
    reply = "**FILE:** pieces/calc.py\n```python\nX = 1\n```\n"
    parsed, refused = nc.parse_files(reply)
    assert refused == [] and parsed == {"pieces/calc.py": "X = 1\n"}
    data = {"model": "m", "choices": [{"message": {"content": reply}, "finish_reason": "stop"}]}
    assert oc.check_reply("writer", "m", data) == reply


def test_one_fence_is_stripped_and_prose_after_it_is_not_file_text():
    parsed, _ = nc.parse_files("FILE: a.py\n```python\nX = 1\n```\nThat is all.\n")
    assert parsed == {"a.py": "X = 1\n"}


def test_a_header_path_still_has_to_pass_the_job_path_rules():
    parsed, refused = nc.parse_files("### FILE: ../evil.py\n```python\nX = 1\n```\n")
    assert parsed == {} and refused == [("../evil.py", "parent folder")]


# ---------------------------------------------------------------- 3. SEARCH/REPLACE edits: the matching rule

def test_an_exact_single_match_applies():
    text = block("pieces/calc.py", "    return 0\n", "    return 50\n")
    changed, refusals = pm.apply_edits(text, {"pieces/calc.py": CALC_BEFORE})
    assert refusals == [] and changed == {"pieces/calc.py": CALC_AFTER}


def test_a_match_after_whitespace_normalization_applies():
    text = block("pieces/calc.py", "return   0  \n", "    return 50\n")
    changed, refusals = pm.apply_edits(text, {"pieces/calc.py": CALC_BEFORE})
    assert refusals == [] and changed == {"pieces/calc.py": CALC_AFTER}


def test_an_ambiguous_match_is_refused_exactly_and_after_normalization():
    content = "x = 0\nx = 0\n"
    changed, refusals = pm.apply_edits(block("a.py", "x = 0\n", "x = 1\n"), {"a.py": content})
    assert changed == {} and refusals == ["a.py: SEARCH text occurs 2 times (ambiguous)"]
    changed, refusals = pm.apply_edits(block("a.py", "x  =  0\n", "x = 1\n"), {"a.py": content})
    assert changed == {} and "ambiguous" in refusals[0]


def test_a_missing_match_is_refused():
    changed, refusals = pm.apply_edits(block("pieces/calc.py", "    return 42\n", "    return 0\n"), {"pieces/calc.py": CALC_BEFORE})
    assert changed == {} and refusals == ["pieces/calc.py: SEARCH text not found"]


def test_one_refused_block_refuses_the_whole_reply_and_a_file_outside_the_job_is_refused():
    text = block("pieces/calc.py", "    return 0\n", "    return 50\n") + block("pieces/calc.py", "    return 42\n", "    return 1\n")
    assert pm.apply_edits(text, {"pieces/calc.py": CALC_BEFORE})[0] == {}
    changed, refusals = pm.apply_edits(block("pieces/other.py", "    return 0\n", "    return 1\n"), {"pieces/calc.py": CALC_BEFORE})
    assert changed == {} and refusals == ["pieces/other.py: not a job file"]


# ---------------------------------------------------------------- 3b. the repair flow: one call, one re-ask, then the whole-file request

def test_a_repair_edit_is_applied_from_one_call(tmp_path):
    root = wp_h_root(tmp_path)
    client_ = PromptClient([block("pieces/calc.py", "    return 0\n", "    return 50\n")])
    models, _ = production(root, client_)
    assert models.call(dots(), "repair", FILES) == {"pieces/calc.py": CALC_AFTER}
    assert client_.calls == [dots()]


def test_a_refused_edit_is_asked_once_more_with_the_example_then_the_whole_file_is_requested(tmp_path):
    root = wp_h_root(tmp_path)
    (root / "pieces" / "calc.py").write_text("def calculate_total(prices):\n    x = 0\n    x = 0\n", encoding="utf-8")
    client_ = PromptClient([block("pieces/calc.py", "    x = 0\n", "    x = 1\n")] * 2 + [ph.file_reply(("pieces/calc.py", CALC_AFTER))])
    models, history = production(root, client_)
    assert models.call(dots(), "repair", FILES) == {"pieces/calc.py": CALC_AFTER}
    assert client_.calls == [dots()] * 3
    assert "SEARCH/REPLACE blocks for the files you change" in client_.users[0]
    assert "Your edits were refused" in client_.users[1] and pm.EDIT_EXAMPLE in client_.users[1]
    assert "SEARCH/REPLACE" not in client_.users[2]                           # the whole-file request asks for complete files
    refused = [json.loads((history / f"failed_call_{n:02d}.json").read_text(encoding="utf-8")) for n in (1, 2)]
    assert [r["call"] for r in refused] == ["repair", "repair"] and all("edits refused" in r["message"] for r in refused)


def test_a_missing_match_then_a_good_re_ask_applies_the_edit(tmp_path):
    root = wp_h_root(tmp_path)
    client_ = PromptClient([block("pieces/calc.py", "    return 42\n", "    return 0\n"), block("pieces/calc.py", "    return 0\n", "    return 50\n")])
    models, _ = production(root, client_)
    assert models.call(dots(), "repair", FILES) == {"pieces/calc.py": CALC_AFTER}
    assert len(client_.calls) == 2


# ---------------------------------------------------------------- 4. the repair digest

def test_the_digest_has_the_assertion_line_stdout_stderr_and_the_test_source(tmp_path):
    (tmp_path / "tests" / "acceptance").mkdir(parents=True)
    (tmp_path / "tests" / "acceptance" / "test_calc_acceptance.py").write_text(
        "def test_sums():\n    assert calculate_total([20, 30]) == 50\n", encoding="utf-8")
    junit = ('<testsuites><testsuite><testcase classname="tests.acceptance.test_calc_acceptance" name="test_sums" '
             'file="tests/acceptance/test_calc_acceptance.py"><failure message="AssertionError">'
             '>       assert calculate_total([20, 30]) == 50\nE       assert 20 == 50</failure>'
             '<system-out>printed the total 20</system-out><system-err>warning: prices look odd</system-err>'
             '</testcase></testsuite></testsuites>')
    text = pm.failing_summary(junit, root=tmp_path)
    assert "E       assert 20 == 50" in text
    assert "printed the total 20" in text and "warning: prices look odd" in text
    assert "test function:" in text and "def test_sums():" in text and "assert calculate_total([20, 30]) == 50" in text


# ---------------------------------------------------------------- 1. raw reply and reasoning files

class ReasoningProvider(ph.Provider):
    """The fake transport, with a reasoning text on every completion reply."""

    def __call__(self, method, url, headers, body, timeout):
        status, headers_, raw = super().__call__(method, url, headers, body, timeout)
        if method != "POST":
            return status, headers_, raw
        data = json.loads(raw)
        data["choices"][0]["message"]["reasoning"] = "I will use key " + ph.FREE_KEY + " here"
        return status, headers_, json.dumps(data).encode()


def test_the_reply_and_reasoning_are_saved_scrubbed_and_linked_from_the_call_record(tmp_path):
    root = wp_h_root(tmp_path)
    provider = ReasoningProvider({dots(): [ph.file_reply(("pieces/calc.py", "# key " + ph.FREE_KEY + "\nx = 1\n"))]})
    models, history = production(root, ph.client(tmp_path, provider), scrub_keys=[ph.FREE_KEY])
    models.call(dots(), "write", ["pieces/calc.py"])
    record = json.loads((history / "call_01.json").read_text(encoding="utf-8"))
    assert record["reply_file"] == "history/reply_01.txt" and record["reasoning_file"] == "history/reasoning_01.txt"
    reply_text = (history / "reply_01.txt").read_text(encoding="utf-8")
    reasoning_text = (history / "reasoning_01.txt").read_text(encoding="utf-8")
    assert "x = 1" in reply_text and reasoning_text == "I will use key [REDACTED] here"
    assert ph.FREE_KEY not in reply_text + reasoning_text


# ---------------------------------------------------------------- 5. repair token cap, no bigger repair retry; roster values

def test_a_repair_call_uses_10000_tokens_and_never_retries_at_a_bigger_size(tmp_path):
    fake = Fake()
    fake.on("/chat/completions", ok(WRITER_OK, finish="length"))
    built, _ = client(tmp_path, fake)
    with pytest.raises(oc.ReplyRejected):
        built.call("writer_a", "system", "user", kind="repair")
    assert [p["body"]["max_tokens"] for p in fake.posts()] == [10000]


def test_a_write_still_retries_a_cut_off_reply_at_72000_tokens(tmp_path):
    fake = Fake()
    fake.on("/chat/completions", ok(WRITER_OK, finish="length"), ok(WRITER_OK))
    built, _ = client(tmp_path, fake)
    built.call("writer_a", "system", "user", kind="writer")
    assert [p["body"]["max_tokens"] for p in fake.posts()] == [40000, 72000]


def test_both_profiles_carry_the_repair_cap_and_writer_b_at_temperature_0_2():
    roster = oc.load_roster_file()
    for name in ("test", "production"):
        assert roster[name]["repair_max_tokens"] == 10000
        assert roster[name]["roles"]["writer_b"]["settings"]["temperature"] == 0.2


# ---------------------------------------------------------------- 6. writer prompt example when reasoning is off

def test_every_writer_and_repair_prompt_ends_with_its_format_example(tmp_path):
    root = wp_h_root(tmp_path)
    models, _ = production(root, FakeClient([]))
    assert models._writer_user("write").endswith(pm.WRITER_EXAMPLE)
    assert models._writer_user("repair").endswith(pm.WRITER_EXAMPLE)
    assert models._writer_user("repair", form="edit").endswith(pm.EDIT_EXAMPLE)
    assert models._writer_user("repair", form="edit-reask", note="x").endswith(pm.EDIT_EXAMPLE)
    assert len(pm.WRITER_EXAMPLE.splitlines()) == 4
