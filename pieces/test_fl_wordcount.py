import os
import sys
import unittest

sys.path.insert(
    0, os.path.join(os.path.dirname(__file__), '..', 'scripts', 'source')
)
from fl_wordcount import word_counts


class TestWordCounts(unittest.TestCase):

    def test_empty_string(self):
        self.assertEqual(word_counts(''), {})

    def test_single_word(self):
        self.assertEqual(word_counts('hello'), {'hello': 1})

    def test_case_insensitive(self):
        self.assertEqual(word_counts('Hello HELLO hello'), {'hello': 3})

    def test_separators(self):
        self.assertEqual(word_counts('hello, world!'), {'hello': 1, 'world': 1})

    def test_digits_included(self):
        self.assertEqual(word_counts('abc123 abc123'), {'abc123': 2})

    def test_mixed_content(self):
        self.assertEqual(
            word_counts('Hello, world! 123 abc.'),
            {'hello': 1, 'world': 1, '123': 1, 'abc': 1},
        )

    def test_punctuation_and_whitespace(self):
        self.assertEqual(
            word_counts('  multiple   spaces\tand\ttabs  '),
            {'multiple': 1, 'spaces': 1, 'and': 1, 'tabs': 1},
        )


if __name__ == '__main__':
    unittest.main()
