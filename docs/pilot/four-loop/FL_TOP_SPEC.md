# fl_top: most frequent words (four-loop job fl-top)

Job fl-top depends on job fl-wordcount. Its module imports word_counts from scripts/source/fl_wordcount.py, which must already be on main when this job starts. Merge fl-wordcount first (see RUNBOOK.md).

- KNOWN: The module is scripts/source/fl_top.py. It uses the Python standard library and `import fl_wordcount` (scripts/source/fl_wordcount.py), calling fl_wordcount.word_counts at call time. It uses no other project module.
- KNOWN: top_words(text, k) takes a str and an int and returns a list of (word, count) tuples.
- KNOWN: The counts are word_counts(text), taken from fl_wordcount.
- KNOWN: The list is sorted by count, highest first; equal counts are sorted by word, A to Z. Only the first k entries are returned.
- KNOWN: k = 0 returns []. If k < 0, top_words raises ValueError.
- KNOWN: The module is read-only: it never prints, creates, changes or deletes any file or folder, and it makes no network call.
