import json
import os


def collect(loop_dir):
    if not os.path.isdir(loop_dir):
        return ([], [])
    
    rows = []
    bad = []
    
    for entry in os.listdir(loop_dir):
        subdir = os.path.join(loop_dir, entry)
        if not os.path.isdir(subdir):
            continue
        status_file = os.path.join(subdir, "status.json")
        if not os.path.isfile(status_file):
            continue
        try:
            with open(status_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if not isinstance(data, dict):
                bad.append(entry)
                continue
            name = data.get('name', '')
            state = data.get('state', '')
            reason = data.get('reason', '')
            coder_calls = data.get('coder_calls', 0)
            updated = data.get('updated', '')
            
            reason = reason[:80]
            
            rows.append({
                'name': name,
                'state': state,
                'reason': reason,
                'coder_calls': coder_calls,
                'updated': updated
            })
        except Exception:
            bad.append(entry)
    
    rows.sort(key=lambda r: r['name'])
    rows.sort(key=lambda r: r['updated'], reverse=True)
    bad.sort()
    
    return (rows, bad)


def main(argv):
    if not argv:
        loop_dir = "_loop"
    else:
        loop_dir = argv[0]
    
    rows, bad = collect(loop_dir)
    
    for row in rows:
        print(f"{row['name']} | {row['state']} | {row['coder_calls']} | {row['reason']}")
    
    if bad:
        print("unreadable: " + ", ".join(bad))
    
    return 0
