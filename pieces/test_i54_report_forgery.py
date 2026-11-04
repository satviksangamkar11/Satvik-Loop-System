"""I54: the controller's pass/fail check does not take a JUnit report at face value. Generated code shares the pytest process that writes the report,
so the check compares the report with the locked test files (the cases it may contain) and the suite's own count.
Each forgery below must fail closed. The trust limit (a forgery consistent in every artifact) is documented in ARCHITECTURE_REVISION_4.
No Docker, no model call."""
import p0_gates as gates

EXPECTED = {"pieces/test_locked_acceptance.py": {"test_one", "test_two"}}


def junit(cases, declared=None):
    body = "".join(f'<testcase classname="{f[:-3].replace("/", ".")}" name="{n}" file="{f}"/>' for f, n in cases)
    count = len(cases) if declared is None else declared
    return f'<?xml version="1.0"?><testsuites><testsuite name="pytest" tests="{count}" failures="0">{body}</testsuite></testsuites>'


HONEST = [("pieces/test_locked_acceptance.py", "test_one"), ("pieces/test_locked_acceptance.py", "test_two")]


def test_an_honest_green_run_passes(tmp_path):
    assert gates.verify_junit(junit(HONEST), EXPECTED, 0, False) == []


def test_a_fabricated_case_from_an_unlocked_file_is_refused(tmp_path):
    forged = junit(HONEST + [("pieces/test_forged.py", "test_invented")])
    problems = gates.verify_junit(forged, EXPECTED, 0, False)
    assert any("not a locked test file" in p and "test_invented" in p for p in problems)


def test_a_report_whose_declared_count_disagrees_with_its_cases_is_refused(tmp_path):
    problems = gates.verify_junit(junit(HONEST, declared=5), EXPECTED, 0, False)
    assert any("reports 5 tests but has 2 cases" in p for p in problems)


def test_a_locked_test_missing_from_the_report_is_still_refused(tmp_path):
    problems = gates.verify_junit(junit(HONEST[:1]), EXPECTED, 0, False)
    assert any("test_two: missing" in p for p in problems)


