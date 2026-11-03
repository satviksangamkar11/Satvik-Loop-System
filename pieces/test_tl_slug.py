import unittest

from scripts.source.tl_slug import slugify


class TestTlSlug(unittest.TestCase):

    def test_empty_string(self):
        self.assertEqual(slugify(''), '')

    def test_only_special_chars(self):
        self.assertEqual(slugify('!!! ???'), '')

    def test_lowercased(self):
        self.assertEqual(slugify('Hello World'), 'hello-world')

    def test_non_ascii_letters_become_separators(self):
        self.assertEqual(slugify('café au lait'), 'caf-au-lait')

    def test_maximal_run_becomes_one_hyphen(self):
        self.assertEqual(slugify('a---b___c'), 'a-b-c')

    def test_leading_and_trailing_hyphens_removed(self):
        self.assertEqual(slugify('---hello---'), 'hello')

    def test_alphanumeric_preserved(self):
        self.assertEqual(slugify('abc123'), 'abc123')

    def test_mixed_content(self):
        self.assertEqual(slugify('Hello, World! 123'), 'hello-world-123')

    def test_idempotent(self):
        for sample in ['', 'hello', 'café au lait', 'a---b', '!!! ???',
                       'Hello, World! 123', 'abc123']:
            with self.subTest(sample=sample):
                once = slugify(sample)
                twice = slugify(once)
                self.assertEqual(once, twice)

    def test_returns_empty_for_no_alphanumeric(self):
        self.assertEqual(slugify('...'), '')
        self.assertEqual(slugify('   '), '')
        self.assertEqual(slugify('\n\t'), '')


if __name__ == '__main__':
    unittest.main()
