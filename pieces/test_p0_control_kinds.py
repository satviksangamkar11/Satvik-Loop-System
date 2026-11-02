"""P0 v2 (2026-11-02): a negative control counts as proof only when the named acceptance test fails with an assertion, or with pytest's own
failure (pytest.fail, pytest.raises' DID NOT RAISE). A control that breaks with NameError, AttributeError, ImportError, SyntaxError, a setup
error or a collection error proves nothing and ends `acceptance-invalid`.

Real pytest runs through the local test double (not the sandbox), on synthetic controls. No model call."""
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts" / "source"))
import p0_gates as gates  # noqa: E402
from test_p0_acceptance import LocalExecutor, NL  # noqa: E402

ACCEPTANCE = "tests/acceptance/test_cart_acceptance.py"
MUST_FAIL = [ACCEPTANCE + "::test_sums_price_times_quantity"]
TEST_BODY = ("from pieces.price_tools import cart_total" + NL
             + "def test_sums_price_times_quantity():" + NL
             + "    assert cart_total([('pen', 1500, 2)]) == 3000" + NL)
RETURNS_ZERO = "def cart_total(items):" + NL + "    return 0" + NL              # wrong code: the acceptance test fails by an assert
RETURNS_RIGHT = "def cart_total(items):" + NL + "    return sum(p * q for _n, p, q in items)" + NL


def control_problems(tmp_path, files):
    root = tmp_path / "r"
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    res = LocalExecutor().run(root, [ACCEPTANCE])
    return gates.must_fail_problems(res.junit, MUST_FAIL, res.returncode, res.timed_out)


def test_an_assertion_failure_on_wrong_code_is_accepted_as_proof(tmp_path):
    assert control_problems(tmp_path, {ACCEPTANCE: TEST_BODY, "pieces/price_tools.py": RETURNS_ZERO}) == []


def test_a_name_error_inside_the_control_is_rejected(tmp_path):
    control = "def cart_total(items):" + NL + "    return undefined_helper(items)" + NL
    problems = control_problems(tmp_path, {ACCEPTANCE: TEST_BODY, "pieces/price_tools.py": control})
    assert problems and "NameError" in problems[0]


def test_an_attribute_error_inside_the_control_is_rejected(tmp_path):
    control = "def cart_total(items):" + NL + "    return (1).items()" + NL
    problems = control_problems(tmp_path, {ACCEPTANCE: TEST_BODY, "pieces/price_tools.py": control})
    assert problems and "AttributeError" in problems[0]


def test_an_import_error_in_the_control_is_rejected(tmp_path):
    control = "from no_such_module_xyz import helper" + NL + "def cart_total(items):" + NL + "    return 0" + NL
    problems = control_problems(tmp_path, {ACCEPTANCE: TEST_BODY, "pieces/price_tools.py": control})
    assert problems and "not collected" in problems[0]


def test_a_syntax_error_in_the_control_is_rejected(tmp_path):
    control = "def cart_total(items:" + NL + "    return 0" + NL
    problems = control_problems(tmp_path, {ACCEPTANCE: TEST_BODY, "pieces/price_tools.py": control})
    assert problems and "not collected" in problems[0]


def test_a_setup_error_is_rejected(tmp_path):
    test = ("import pytest" + NL + "@pytest.fixture" + NL + "def cart():" + NL + "    raise RuntimeError('fixture broke')" + NL
            + "def test_sums_price_times_quantity(cart):" + NL + "    assert 0 == 3000" + NL)
    problems = control_problems(tmp_path, {ACCEPTANCE: test, "pieces/price_tools.py": RETURNS_ZERO})
    assert problems and "(error)" in problems[0]


def test_a_control_that_passes_the_test_is_rejected(tmp_path):
    problems = control_problems(tmp_path, {ACCEPTANCE: TEST_BODY, "pieces/price_tools.py": RETURNS_RIGHT})
    assert problems and "did not fail" in problems[0]


@pytest.mark.parametrize("body", [
    "import pytest" + NL + "def test_sums_price_times_quantity():" + NL + "    pytest.fail('wrong total')" + NL,
    "import pytest" + NL + "def test_sums_price_times_quantity():" + NL + "    with pytest.raises(ValueError):" + NL + "        pass" + NL,
])
def test_pytest_own_failures_count_as_proof(tmp_path, body):
    assert control_problems(tmp_path, {ACCEPTANCE: body}) == []
