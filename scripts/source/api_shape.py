def shape_diff(expected, actual, path='$'):
    """Compare two JSON-like values and return a list of shape problems.
    
    Returns an empty list if the shapes match.
    """
    problems = []
    
    # None matches anything (unknown is null)
    if expected is None or actual is None:
        return problems
    
    # Dict comparison
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            problems.append(f'type {path}: expected dict')
            return problems
        exp_keys = sorted(expected.keys())
        act_keys = sorted(actual.keys())
        for key in exp_keys:
            if key not in actual:
                problems.append(f'missing {path}.{key}')
        for key in act_keys:
            if key not in expected:
                problems.append(f'extra {path}.{key}')
        for key in exp_keys:
            if key in actual:
                problems.extend(shape_diff(expected[key], actual[key], f'{path}.{key}'))
        return problems
    
    # List comparison
    if isinstance(expected, list):
        if not isinstance(actual, list):
            problems.append(f'type {path}: expected list')
            return problems
        if len(expected) == 0:
            return problems
        for i, act_item in enumerate(actual):
            problems.extend(shape_diff(expected[0], act_item, f'{path}[{i}]'))
        return problems
    
    # Bool comparison
    if isinstance(expected, bool):
        if not isinstance(actual, bool):
            problems.append(f'type {path}: expected bool')
            return problems
        return problems
    
    # Number comparison
    if isinstance(expected, (int, float)):
        if isinstance(actual, bool):
            problems.append(f'type {path}: expected number')
            return problems
        if not isinstance(actual, (int, float)):
            problems.append(f'type {path}: expected number')
            return problems
        return problems
    
    # String comparison
    if isinstance(expected, str):
        if not isinstance(actual, str):
            problems.append(f'type {path}: expected str')
            return problems
        return problems
    
    return problems
