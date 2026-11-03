import json
import os
import shutil
import sys
import tempfile
import time
import unittest

# Make the module under test importable regardless of the working directory.
sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'scripts', 'source'),
)

from api_idempotency import IdempotencyStore


class TestIdempotencyStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._clock_now = [0.0]

        def clock():
            return self._clock_now[0]

        self.clock = clock
        self.store = IdempotencyStore(self.tmp, ttl_s=100, clock=clock)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # --- basic behaviour -------------------------------------------------

    def test_put_get_roundtrip(self):
        resp = {'status': 200, 'body': 'hello'}
        self.assertEqual(self.store.put('valid_key_1', resp), resp)
        self.assertEqual(self.store.get('valid_key_1'), resp)

    def test_first_write_wins(self):
        resp1 = {'v': 1}
        resp2 = {'v': 2}
        self.store.put('first_key_1', resp1)
        self.store.put('first_key_1', resp2)
        self.assertEqual(self.store.get('first_key_1'), resp1)

    def test_get_missing_key_returns_none(self):
        self.assertIsNone(self.store.get('missing_key_1'))

    def test_stored_file_contains_ts_and_response(self):
        self.store.put('atomic_key_1', {'c': 3})
        path = os.path.join(self.tmp, 'atomic_key_1.json')
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        self.assertIn('ts', data)
        self.assertEqual(data['response'], {'c': 3})

    # --- key validation --------------------------------------------------

    def test_invalid_key_too_short_raises(self):
        with self.assertRaises(ValueError):
            self.store.put('short', {'a': 1})
        with self.assertRaises(ValueError):
            self.store.get('short')

    def test_invalid_key_too_long_raises(self):
        long_key = 'a' * 129
        with self.assertRaises(ValueError):
            self.store.put(long_key, {'a': 1})
        with self.assertRaises(ValueError):
            self.store.get(long_key)

    def test_invalid_key_bad_chars_raise(self):
        bad_keys = ['bad key!!!', 'key with spaces', 'key@host', 'key/here', '']
        for bad in bad_keys:
            with self.assertRaises(ValueError, msg='key=%r' % bad):
                self.store.put(bad, {'a': 1})
            with self.assertRaises(ValueError, msg='key=%r' % bad):
                self.store.get(bad)

    def test_valid_key_boundary_lengths(self):
        # exactly 8 and exactly 128 characters are valid
        k8 = 'a' * 8
        k128 = 'b' * 128
        self.store.put(k8, {'n': 8})
        self.store.put(k128, {'n': 128})
        self.assertEqual(self.store.get(k8), {'n': 8})
        self.assertEqual(self.store.get(k128), {'n': 128})

    def test_non_string_key_raises(self):
        with self.assertRaises(ValueError):
            self.store.put(12345678, {'a': 1})
        with self.assertRaises(ValueError):
            self.store.get(None)

    # --- TTL and expiry --------------------------------------------------

    def test_entry_expires_after_ttl(self):
        self.store.put('exp_key_1', {'x': 1})
        self._clock_now[0] = 200  # beyond ttl of 100
        self.assertIsNone(self.store.get('exp_key_1'))

    def test_put_overwrites_expired_entry(self):
        self.store.put('exp_key_2', {'x': 1})
        self._clock_now[0] = 200
        new_resp = {'x': 2}
        result = self.store.put('exp_key_2', new_resp)
        self.assertEqual(result, new_resp)
        self.assertEqual(self.store.get('exp_key_2'), new_resp)

    def test_within_ttl_is_not_expired(self):
        self.store.put('fresh_key', {'b': 2})
        self._clock_now[0] = 99  # still <= ttl of 100
        self.assertEqual(self.store.get('fresh_key'), {'b': 2})

    def test_default_ttl_is_86400(self):
        store = IdempotencyStore(
            os.path.join(self.tmp, 'default_dir'), clock=self.clock
        )
        self._clock_now[0] = 1000
        store.put('default_key', {'b': 2})
        self._clock_now[0] = 1000 + 86399
        self.assertEqual(store.get('default_key'), {'b': 2})
        self._clock_now[0] = 1000 + 86401
        self.assertIsNone(store.get('default_key'))

    # --- corruption / unreadable files -----------------------------------

    def test_unreadable_json_treated_as_absent(self):
        path = os.path.join(self.tmp, 'corrupt_key.json')
        with open(path, 'w', encoding='utf-8') as f:
            f.write('not valid json{{{')
        self.assertIsNone(self.store.get('corrupt_key'))
        # put should overwrite the corrupt file
        resp = {'ok': True}
        result = self.store.put('corrupt_key', resp)
        self.assertEqual(result, resp)
        self.assertEqual(self.store.get('corrupt_key'), resp)

    def test_missing_ts_treated_as_absent(self):
        path = os.path.join(self.tmp, 'nots_key.json')
        with open(path, 'w', encoding='utf-8') as f:
            json.dump({'response': {'a': 1}}, f)  # no 'ts'
        self.assertIsNone(self.store.get('nots_key'))

    # --- folder creation -------------------------------------------------

    def test_folder_is_created_when_missing(self):
        new_dir = os.path.join(self.tmp, 'a', 'b', 'c')
        store = IdempotencyStore(new_dir, clock=self.clock)
        store.put('nested_key_1', {'a': 1})
        self.assertEqual(store.get('nested_key_1'), {'a': 1})
        self.assertTrue(os.path.isdir(new_dir))

    # --- atomic writes ---------------------------------------------------

    def test_file_written_whole_via_tempfile_and_replace(self):
        self.store.put('atomic_key_1', {'c': 3})
        path = os.path.join(self.tmp, 'atomic_key_1.json')
        # No leftover temp files should remain in the folder.
        leftovers = [
            f for f in os.listdir(self.tmp)
            if f != 'atomic_key_1.json'
        ]
        self.assertEqual(leftovers, [])
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        self.assertEqual(data['response'], {'c': 3})
        self.assertIn('ts', data)


if __name__ == '__main__':
    unittest.main()
