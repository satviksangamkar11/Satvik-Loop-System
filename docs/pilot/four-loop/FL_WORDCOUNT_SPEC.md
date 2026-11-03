# fl_wordcount: count words (four-loop job fl-wordcount)

- KNOWN: The module is scripts/source/fl_wordcount.py and uses only the Python standard library.
- KNOWN: word_counts(text) takes a str and returns a dict from word to count.
- KNOWN: A word is a maximal run of the characters a-z and 0-9 after the text is lower-cased; every other character separates words.
- KNOWN: word_counts('') returns {}.
- KNOWN: The module is read-only: it never prints, creates, changes or deletes any file or folder, and it makes no network call.
