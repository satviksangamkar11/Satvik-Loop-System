# fl_clamp: clamp a number into a range (four-loop job fl-clamp)

- KNOWN: The module is scripts/source/fl_clamp.py and uses only the Python standard library.
- KNOWN: clamp(x, lo, hi) takes three numbers (int or float) and returns one number.
- KNOWN: If lo > hi, clamp raises ValueError.
- KNOWN: If x < lo it returns lo; if x > hi it returns hi; otherwise it returns x unchanged.
- KNOWN: The module is read-only: it never prints, creates, changes or deletes any file or folder, and it makes no network call.
