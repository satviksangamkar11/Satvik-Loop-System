# fl_chunk: split a list into chunks (four-loop job fl-chunk)

- KNOWN: The module is scripts/source/fl_chunk.py and uses only the Python standard library.
- KNOWN: chunks(items, size) takes a list and an int and returns a list of lists.
- KNOWN: The items keep their order; every chunk has exactly size items except the last, which holds what is left (1 to size items).
- KNOWN: chunks([], size) returns [] for any valid size.
- KNOWN: If size < 1, chunks raises ValueError.
- KNOWN: The module is read-only: it never prints, creates, changes or deletes any file or folder, and it makes no network call.
