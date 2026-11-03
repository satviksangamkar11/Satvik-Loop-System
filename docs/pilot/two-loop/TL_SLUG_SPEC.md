# tl_slug: text to a URL slug (two-loop job tl-slug)

- KNOWN: The module is scripts/source/tl_slug.py and uses only the Python standard library.
- KNOWN: slugify(text) takes one str and returns one str.
- KNOWN: The text is lower-cased first (str.lower()).
- KNOWN: Every maximal run of characters that are not a to z or 0 to 9 (after lower-casing) becomes one "-". Non-ASCII letters count as such characters: slugify("café au lait") == "caf-au-lait".
- KNOWN: Leading and trailing "-" are removed.
- KNOWN: slugify returns "" when the text has no a to z and no 0 to 9 character (for example "" or "!!! ???").
- KNOWN: slugify applied to its own result returns that result unchanged.
- KNOWN: The module is read-only: it never creates, changes or deletes any file or folder, and it makes no network call.
