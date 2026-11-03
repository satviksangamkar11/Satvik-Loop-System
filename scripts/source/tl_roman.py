"""Integer to Roman numeral conversion."""

def to_roman(n):
    """Convert an integer to a Roman numeral.

    Args:
        n: Integer between 1 and 3999 inclusive.

    Returns:
        The standard subtractive Roman numeral string.

    Raises:
        ValueError: If n is not an integer between 1 and 3999.
    """
    if not isinstance(n, int):
        raise ValueError("Input must be an integer")
    if n < 1 or n > 3999:
        raise ValueError("Input must be between 1 and 3999")

    pieces = [
        (1000, "M"),
        (900, "CM"),
        (500, "D"),
        (400, "CD"),
        (100, "C"),
        (90, "XC"),
        (50, "L"),
        (40, "XL"),
        (10, "X"),
        (9, "IX"),
        (5, "V"),
        (4, "IV"),
        (1, "I"),
    ]

    parts = []
    for value, numeral in pieces:
        while n >= value:
            parts.append(numeral)
            n -= value

    return "".join(parts)
