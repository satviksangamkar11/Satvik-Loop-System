"""Exact-bytes text I/O for the contents of job, original, candidate, green, attempt and review files (I69, I73-I75).

Text mode is wrong for these files on Windows: reading turns each \\r\\n into \\n, and writing turns each \\n into \\r\\n. A file that is
compared, hashed or restored must be read and written as its utf-8 bytes, so the disk always holds exactly the text the job holds.
JSON config and status files stay with atomic_json.
"""
from pathlib import Path


def read_text_exact(path, errors="strict") -> str:
    """The file's text exactly as its bytes are (utf-8, no newline translation). errors="replace" for the reads that were tolerant before."""
    return Path(path).read_bytes().decode("utf-8", errors)


def write_text_exact(path, text) -> None:
    """Writes the text as exactly its utf-8 bytes."""
    Path(path).write_bytes(text.encode("utf-8"))


def line_ends_as_lf(text) -> str:
    """The text with every \\r\\n and lone \\r read as \\n. For comparing a model's reply with a file on disk (I76): the reply is always LF,
    the disk may hold CRLF. Hashes and restores never use this: they stay on the exact bytes."""
    return text.replace("\r\n", "\n").replace("\r", "\n")
