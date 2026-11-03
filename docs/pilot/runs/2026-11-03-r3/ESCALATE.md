# Escalation Report

Job: pilot-loop-status

State: escalate

Reason: Xing repair is unavailable

Last test output (last 9000 chars):
```
.FFFFFFFFFFFFF.F.                                                        [100%]
=================================== FAILURES ===================================
______________________________ test_normal_order _______________________________
pieces/test_loop_status.py:25: in test_normal_order
    make_status(str(f1), name='A', state='running', reason='ok', updated='2025-01-01', coder_calls=3)
pieces/test_loop_status.py:13: in make_status
    with open(path, 'w', encoding='utf-8') as fh:
         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
E   FileNotFoundError: [Errno 2] No such file or directory: '/tmp/pytest-of-nobody/pytest-0/test_normal_order0/a/status.json'
______________________________ test_invalid_json _______________________________
pieces/test_loop_status.py:45: in test_invalid_json
    assert bad == ['bad']
E   AssertionError: assert [] == ['bad']
E     
E     Right contains one more item: 'bad'
E     Use -v to get more diff
_______________________________ test_not_object ________________________________
pieces/test_loop_status.py:56: in test_not_object
    assert bad == ['arr']
E   AssertionError: assert [] == ['arr']
E     
E     Right contains one more item: 'arr'
E     Use -v to get more diff
_______________________________ test_reason_cut ________________________________
pieces/test_loop_status.py:64: in test_reason_cut
    assert len(rows) == 1
E   assert 0 == 1
E    +  where 0 = len([])
_____________________________ test_missing_fields ______________________________
pieces/test_loop_status.py:73: in test_missing_fields
    assert len(rows) == 1
E   assert 0 == 1
E    +  where 0 = len([])
_______________________________ test_bad_sorted ________________________________
pieces/test_loop_status.py:85: in test_bad_sorted
    assert bad == ['a', 'm', 'z']
E   AssertionError: assert [] == ['a', 'm', 'z']
E     
E     Right contains 3 more items, first extra item: 'a'
E     Use -v to get more diff
_______________________________ test_main_output _______________________________
pieces/test_loop_status.py:99: in test_main_output
    assert lines[0] == 'A | run | 2 | r'
           ^^^^^^^^
E   IndexError: list index out of range
________________________________ test_main_bad _________________________________
pieces/test_loop_status.py:112: in test_main_bad
    assert captured.out.startswith('unreadable: bad')
E   AssertionError: assert False
E    +  where False = <built-in method startswith of str object at 0x7ff8e7c6c240>('unreadable: bad')
E    +    where <built-in method startswith of str object at 0x7ff8e7c6c240> = ''.startswith
E    +      where '' = CaptureResult(out='', err='').out
__________________________ test_rows_order_and_fields __________________________
tests/acceptance/test_loop_status_acceptance.py:35: in test_rows_order_and_fields
    assert [r["name"] for r in rows] == ["b", "a"]
E   AssertionError: assert [] == ['b', 'a']
E     
E     Right contains 2 more items, first extra item: 'b'
E     Use -v to get more diff
_________________________ test_missing_fields_default __________________________
tests/acceptance/test_loop_status_acceptance.py:47: in test_missing_fields_default
    assert rows == [{"name": "only", "state": "", "reason": "", "coder_calls": 0, "updated": ""}]
E   AssertionError: assert [] == [{'name': 'on...lls': 0, ...}]
E     
E     Right contains one more item: {'name': 'only', 'state': '', 'reason': '', 'coder_calls': 0, ...}
E     Use -v to get more diff
______________________________ test_not_an_object ______________________________
tests/acceptance/test_loop_status_acceptance.py:56: in test_not_an_object
    assert bad == ["list"]
E   AssertionError: assert [] == ['list']
E     
E     Right contains one more item: 'list'
E     Use -v to get more diff
___________________________ test_tie_sorted_by_name ____________________________
tests/acceptance/test_loop_status_acceptance.py:65: in test_tie_sorted_by_name
    assert [r["name"] for r in rows] == ["alpha", "zeta"]
E   AssertionError: assert [] == ['alpha', 'zeta']
E     
E     Right contains 2 more items, first extra item: 'alpha'
E     Use -v to get more diff
__________________________ test_deeper_files_ignored ___________________________
tests/acceptance/test_loop_status_acceptance.py:74: in test_deeper_files_ignored
    assert [r["name"] for r in rows] == ["a"]
E   AssertionError: assert [] == ['a']
E     
E     Right contains one more item: 'a'
E     Use -v to get more diff
_______________________________ test_main_output _______________________________
tests/acceptance/test_loop_status_acceptance.py:87: in test_main_output
    assert len(out) == 3
E   assert 0 == 3
E    +  where 0 = len([])
=========================== short test summary info ============================
FAILED pieces/test_loop_status.py::test_normal_order - FileNotFoundError: [Er...
FAILED pieces/test_loop_status.py::test_invalid_json - AssertionError: assert...
FAILED pieces/test_loop_status.py::test_not_object - AssertionError: assert [...
FAILED pieces/test_loop_status.py::test_reason_cut - assert 0 == 1
FAILED pieces/test_loop_status.py::test_missing_fields - assert 0 == 1
FAILED pieces/test_loop_status.py::test_bad_sorted - AssertionError: assert [...
FAILED pieces/test_loop_status.py::test_main_output - IndexError: list index ...
FAILED pieces/test_loop_status.py::test_main_bad - AssertionError: assert False
FAILED tests/acceptance/test_loop_status_acceptance.py::test_rows_order_and_fields
FAILED tests/acceptance/test_loop_status_acceptance.py::test_missing_fields_default
FAILED tests/acceptance/test_loop_status_acceptance.py::test_not_an_object - ...
FAILED tests/acceptance/test_loop_status_acceptance.py::test_tie_sorted_by_name
FAILED tests/acceptance/test_loop_status_acceptance.py::test_deeper_files_ignored
FAILED tests/acceptance/test_loop_status_acceptance.py::test_main_output - as...
14 failed, 3 passed in 0.61s

```
