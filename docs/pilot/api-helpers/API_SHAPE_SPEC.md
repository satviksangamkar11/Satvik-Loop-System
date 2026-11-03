# api_shape: JSON shape comparer (loop job ah-shape)

- KNOWN: The module is scripts/source/api_shape.py and uses only the Python standard library.
- KNOWN: shape_diff(expected, actual, path='$') compares two JSON-like values and returns a list of str problems; [] means the shapes match.
- KNOWN: A dict must have exactly the same keys: each key of expected missing from actual gives 'missing <path>.<key>', and each extra key in actual gives 'extra <path>.<key>'. Matching keys are compared recursively, in sorted key order, with path '<path>.<key>'.
- KNOWN: A list in expected: when expected is empty any list matches; otherwise each item of actual is compared with expected[0], with path '<path>[<i>]'. A non-list actual gives 'type <path>: expected list'.
- KNOWN: None on either side matches anything (unknown is null).
- KNOWN: bool, int/float (numbers match each other), str, dict and list are the types; a mismatch gives 'type <path>: expected <name>' with name one of bool, number, str, dict, list. A bool never matches a number.
- KNOWN: Problems are returned in the order they are found.
- KNOWN: The module makes no network call and prints nothing.
