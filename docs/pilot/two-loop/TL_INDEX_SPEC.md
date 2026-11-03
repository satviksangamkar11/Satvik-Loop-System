# tl_index: anchors for a list of titles (two-loop job tl-index)

Job tl-index depends on job tl-slug. Its module imports slugify from scripts/source/tl_slug.py, which must already be on main when this job starts. Merge tl-slug first (see RUNBOOK.md).

- KNOWN: The module is scripts/source/tl_index.py. It uses the Python standard library and `from tl_slug import slugify` (scripts/source/tl_slug.py). It uses no other project module.
- KNOWN: index_entries(titles) takes a list of str and returns a list of (anchor, title) tuples, in the same order as the input. Each title is returned unchanged.
- KNOWN: The anchor is slugify(title), taken from tl_slug. If slugify returns "", the anchor is "section".
- KNOWN: Occurrences of the same base anchor are numbered in order. The first keeps the base anchor. The k-th occurrence (k = 2, 3, ...) becomes the base anchor, a "-", and str(k).
- KNOWN: The numbering does not check whether a suffixed anchor clashes with a base anchor that appears later. That case is not handled.
- KNOWN: index_entries([]) returns [].
- KNOWN: The module is read-only: it never creates, changes or deletes any file or folder, and it makes no network call.
