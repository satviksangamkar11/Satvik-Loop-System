import unittest
from scripts.source.fl_dedupe import dedupe

class TestDedupe(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(dedupe([]), [])
    def test_single(self):
        self.assertEqual(dedupe([1]), [1])
    def test_duplicates(self):
        self.assertEqual(dedupe([1, 2, 2, 3, 1]), [1, 2, 3])
    def test_order(self):
        self.assertEqual(dedupe([2, 1, 2, 1, 3]), [2, 1, 3])
    def test_strings(self):
        self.assertEqual(dedupe(['a', 'b', 'a', 'c']), ['a', 'b', 'c'])
    def test_tuples(self):
        self.assertEqual(dedupe([(1, 2), (3, 4), (1, 2)]), [(1, 2), (3, 4)])

if __name__ == '__main__':
    unittest.main()
