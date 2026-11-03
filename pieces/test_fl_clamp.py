import unittest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts', 'source'))
from fl_clamp import clamp

class TestClamp(unittest.TestCase):
    def test_below_lo(self):
        self.assertEqual(clamp(0, 2, 5), 2)
    def test_above_hi(self):
        self.assertEqual(clamp(10, 2, 5), 5)
    def test_within(self):
        self.assertEqual(clamp(3, 2, 5), 3)
    def test_equal_lo(self):
        self.assertEqual(clamp(2, 2, 5), 2)
    def test_equal_hi(self):
        self.assertEqual(clamp(5, 2, 5), 5)
    def test_floats(self):
        self.assertAlmostEqual(clamp(1.5, 1.0, 2.0), 1.5)
    def test_lo_greater_than_hi(self):
        with self.assertRaises(ValueError):
            clamp(1, 5, 2)

if __name__ == '__main__':
    unittest.main()
