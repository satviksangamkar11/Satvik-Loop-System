# loop_status: summary of loop job results

- KNOWN: The module is scripts/source/loop_status.py and uses only the Python standard library.
- KNOWN: collect(loop_dir) reads only files matching loop_dir/*/status.json (exactly one folder level; deeper files are ignored).
- KNOWN: collect returns (rows, bad). rows is a list of dicts with exactly the keys name, state, reason, coder_calls, updated.
- KNOWN: name, state, reason and updated come from the status file; a missing text field becomes "" and a missing coder_calls becomes 0.
- KNOWN: reason is cut to its first 80 characters.
- KNOWN: rows are sorted by updated, newest first; equal updated values are sorted by name, A to Z.
- KNOWN: a status.json that cannot be read, is not valid JSON, or is not a JSON object is skipped, and its folder name is added to bad; bad is sorted A to Z.
- KNOWN: if loop_dir does not exist, collect returns ([], []).
- KNOWN: main(argv) takes an optional loop folder (default "_loop"), prints one line per row as "name | state | coder_calls | reason", then, only if bad is not empty, one line "unreadable: " followed by the bad names joined by ", ", and returns 0.
- KNOWN: the module is read-only: it never creates, changes or deletes any file or folder.
