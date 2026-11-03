import unittest
from scripts.source.api_shape import shape_diff


class TestApiShape(unittest.TestCase):
    
    def test_none_matches_anything(self):
        self.assertEqual(shape_diff(None, 1), [])
        self.assertEqual(shape_diff(1, None), [])
        self.assertEqual(shape_diff(None, None), [])
        self.assertEqual(shape_diff(None, {'a': 1}), [])
        self.assertEqual(shape_diff({'a': 1}, None), [])
    
    def test_dict_exact_match(self):
        self.assertEqual(shape_diff({'a': 1, 'b': 'hello'}, {'a': 1, 'b': 'hello'}), [])
    
    def test_dict_missing_key(self):
        result = shape_diff({'a': 1, 'b': 2}, {'a': 1})
        self.assertEqual(result, ['missing $.b'])
    
    def test_dict_extra_key(self):
        result = shape_diff({'a': 1}, {'a': 1, 'b': 2})
        self.assertEqual(result, ['extra $.b'])
    
    def test_dict_missing_and_extra(self):
        result = shape_diff({'a': 1, 'b': 2}, {'a': 1, 'c': 3})
        self.assertEqual(result, ['missing $.b', 'extra $.c'])
    
    def test_dict_nested(self):
        result = shape_diff({'a': {'x': 1}}, {'a': {'y': 2}})
        self.assertEqual(result, ['missing $.a.x', 'extra $.a.y'])
    
    def test_dict_sorted_keys(self):
        result = shape_diff({'b': 1, 'a': 2}, {'a': 2})
        self.assertEqual(result, ['missing $.b'])
    
    def test_dict_type_mismatch(self):
        result = shape_diff({'a': 1}, 'not a dict')
        self.assertEqual(result, ['type $: expected dict'])
    
    def test_list_empty_matches_any(self):
        self.assertEqual(shape_diff([], [1, 2, 3]), [])
        self.assertEqual(shape_diff([], []), [])
    
    def test_list_nonempty_compares_first(self):
        self.assertEqual(shape_diff([1], [1, 2, 3]), [])
        self.assertEqual(shape_diff([{'a': 1}], [{'a': 1}, {'a': 2}]), [])
    
    def test_list_type_mismatch(self):
        result = shape_diff([1], 'not a list')
        self.assertEqual(result, ['type $: expected list'])
    
    def test_list_nested(self):
        result = shape_diff([{'a': 1}], [{'b': 2}])
        self.assertEqual(result, ['missing $[0].a', 'extra $[0].b'])
    
    def test_bool_matches_bool(self):
        self.assertEqual(shape_diff(True, True), [])
        self.assertEqual(shape_diff(False, False), [])
    
    def test_bool_never_matches_number(self):
        result = shape_diff(True, 1)
        self.assertEqual(result, ['type $: expected bool'])
        
        result = shape_diff(1, True)
        self.assertEqual(result, ['type $: expected number'])
    
    def test_bool_type_mismatch(self):
        result = shape_diff(True, 'hello')
        self.assertEqual(result, ['type $: expected bool'])
        
        result = shape_diff(False, 1)
        self.assertEqual(result, ['type $: expected bool'])
    
    def test_number_matches_number(self):
        self.assertEqual(shape_diff(1, 1), [])
        self.assertEqual(shape_diff(1, 2), [])
        self.assertEqual(shape_diff(1.5, 1), [])
        self.assertEqual(shape_diff(1, 1.5), [])
    
    def test_number_type_mismatch(self):
        result = shape_diff(1, 'hello')
        self.assertEqual(result, ['type $: expected number'])
        
        result = shape_diff(1, True)
        self.assertEqual(result, ['type $: expected number'])
        
        result = shape_diff(1, {'a': 1})
        self.assertEqual(result, ['type $: expected number'])
        
        result = shape_diff(1, [1])
        self.assertEqual(result, ['type $: expected number'])
    
    def test_str_matches_str(self):
        self.assertEqual(shape_diff('hello', 'hello'), [])
        self.assertEqual(shape_diff('hello', 'world'), [])
    
    def test_str_type_mismatch(self):
        result = shape_diff('hello', 1)
        self.assertEqual(result, ['type $: expected str'])
        
        result = shape_diff('hello', True)
        self.assertEqual(result, ['type $: expected str'])
        
        result = shape_diff('hello', {'a': 1})
        self.assertEqual(result, ['type $: expected str'])
        
        result = shape_diff('hello', [1])
        self.assertEqual(result, ['type $: expected str'])
    
    def test_problems_in_order(self):
        result = shape_diff({'a': 1, 'b': 2}, {'a': 'x', 'c': 3})
        self.assertEqual(result, ['missing $.b', 'extra $.c', 'type $.a: expected number'])
    
    def test_nested_dict_in_list(self):
        result = shape_diff([{'a': 1}], [{'a': 1}, {'b': 2}])
        self.assertEqual(result, ['missing $[1].a', 'extra $[1].b'])
    
    def test_deep_nested(self):
        expected = {'users': [{'name': 'Alice', 'age': 30}]}
        actual = {'users': [{'name': 'Alice', 'age': 30}, {'name': 'Bob', 'age': 25}]}
        self.assertEqual(shape_diff(expected, actual), [])


if __name__ == '__main__':
    unittest.main()
