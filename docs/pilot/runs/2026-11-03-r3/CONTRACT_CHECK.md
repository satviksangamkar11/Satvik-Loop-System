# CONTRACT CHECK

Dots and Xing both fail the same locked acceptance test, and their code differs. The spec and the locked acceptance test disagree or the spec is unclear. The ladder goes on; the repair step may still run.

## tests/acceptance/test_loop_status_acceptance.py::test_deeper_files_ignored
- Assertion message: `AssertionError: assert [] == ['a']
  
  Right contains one more item: 'a'
  Use -v to get more diff`
- likely SPEC line (guessed by word overlap): SPEC 2: - KNOWN: collect(loop_dir) reads only files matching loop_dir/*/status.json (exactly one folder level; deeper files are ignored).

## tests/acceptance/test_loop_status_acceptance.py::test_main_output
- Assertion message: `assert 0 == 3
 +  where 0 = len([])`
- likely SPEC line (guessed by word overlap): SPEC 9: - KNOWN: main(argv) takes an optional loop folder (default "_loop"), prints one line per row as "name | state | coder_calls | reason", then, only if bad is not empty, one line "unreadable: " followed by the bad names joined by ", ", and returns 0.
