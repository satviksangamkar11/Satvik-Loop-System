# api_idempotency: idempotency store (loop job ah-idempotency)

- KNOWN: The module is scripts/source/api_idempotency.py and uses only the Python standard library.
- KNOWN: IdempotencyStore(folder, ttl_s=86400, clock=time.time) keeps one JSON file per key in folder (created when missing).
- KNOWN: A valid key is a str of 8 to 128 characters, each a letter, a digit, '-' or '_'. Any other key makes get and put raise ValueError.
- KNOWN: put(key, response) stores the dict response with the time clock() and returns it. If a stored, unexpired response for key already exists, put does not change it and returns the stored response (the first write wins).
- KNOWN: get(key) returns the stored response dict, or None when there is none or when it is older than ttl_s seconds by clock().
- KNOWN: An expired entry is treated as absent, so put stores the new response over it.
- KNOWN: Each file is written whole: a temporary file in the same folder, then os.replace. A file that cannot be read as JSON counts as absent.
- KNOWN: The module makes no network call and prints nothing.
