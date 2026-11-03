# fl_dedupe: remove duplicates, keep order (four-loop job fl-dedupe)

- KNOWN: The module is scripts/source/fl_dedupe.py and uses only the Python standard library.
- KNOWN: dedupe(items) takes a list of hashable values and returns a new list.
- KNOWN: Each value appears once, at the position of its first occurrence; the order of first occurrences is kept.
- KNOWN: dedupe([]) returns [].
- KNOWN: The input list is not changed.
- KNOWN: The module is read-only: it never prints, creates, changes or deletes any file or folder, and it makes no network call.
