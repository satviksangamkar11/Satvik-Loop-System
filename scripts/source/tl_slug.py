import re


def slugify(text):
    """Convert text to a URL slug.

    The text is lower-cased, then every maximal run of characters that
    are not a-z or 0-9 becomes a single '-'.  Leading and trailing
    '-' are removed.  Returns '' when the text contains no a-z or
    0-9 character.
    """
    text = text.lower()
    text = re.sub(r'[^a-z0-9]+', '-', text)
    text = text.strip('-')
    return text
