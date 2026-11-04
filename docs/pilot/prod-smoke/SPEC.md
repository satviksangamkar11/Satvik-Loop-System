# ps-duration: format a duration in seconds as "HHh MMm SSs" (production smoke job ps-duration)

- KNOWN: The module is scripts/prod_smoke/duration_text.py and uses only the Python standard library.
- KNOWN: It defines format_duration(seconds), which takes one whole number of seconds (an int that is not a bool) and returns one str.
- KNOWN: The result has the form "<h>h <mm>m <ss>s". Hours are not zero-padded and not capped; minutes and seconds are always two digits.
- KNOWN: format_duration(0) returns "0h 00m 00s"; format_duration(59) returns "0h 00m 59s"; format_duration(3725) returns "1h 02m 05s".
- KNOWN: format_duration(360000) returns "100h 00m 00s" (large values are not refused).
- KNOWN: A negative number raises ValueError.
- KNOWN: A float, a string or a bool raises TypeError.
- KNOWN: The module is read-only: it never prints, opens, creates, changes or deletes any file or folder, and it makes no network call.
