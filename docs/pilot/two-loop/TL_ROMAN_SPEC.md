# tl_roman: integer to Roman numeral (two-loop job tl-roman)

- KNOWN: The module is scripts/source/tl_roman.py and uses only the Python standard library.
- KNOWN: to_roman(n) takes one int and returns one str: the standard Roman numeral of n, using only the letters I, V, X, L, C, D and M.
- KNOWN: The standard form is subtractive: 4 is IV, 9 is IX, 40 is XL, 90 is XC, 400 is CD and 900 is CM. The numeral is the greedy one: take the largest value first, using the pairs M, CM, D, CD, C, XC, L, XL, X, IX, V, IV, I. So 1994 is MCMXCIV and 3999 is MMMCMXCIX.
- KNOWN: n from 1 to 3999 inclusive is valid. Any other n (0, negative numbers, 4000 and up) raises ValueError.
- KNOWN: The module is read-only: it never prints, creates, changes or deletes any file or folder, and it makes no network call.
