import unittest
import sys
import os

sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'scripts', 'source'))
from fl_chunk import chunks

class TestChunks(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(chunks([], 3), [])

    def test_exact_chunk(self):
        self.assertEqual(chunks([1, 2, 3], 3), [[1, 2, 3]])

    def test_multiple_chunks(self):
        self.assertEqual(chunks([1, 2, 3, 4, 5], 2), [[1, 2], [3, 4], [5]])

    def test_size_one(self):
        self.assertEqual(chunks([1, 2, 3], 1), [[1], [2], [3]])

    def test_invalid_size(self):
        with self.assertRaises(ValueError):
            chunks([1, 2, 3], 0)
        with self.assertRaises(ValueError):
            chunks([1, 2, 3], -1)

if __name__ == '__main__':
    unittest.main()
