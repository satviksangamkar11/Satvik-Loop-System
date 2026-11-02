"""A stand-in for the `claude` executable, used ONLY by pieces/test_plan_job.py (P1c, 2026-11-02). It never calls a model.

It appends one line to FAKE_CLAUDE_LOG for each run (the arguments, the working folder and its entries, and whether ANTHROPIC_API_KEY was
in the environment), then prints the file named by FAKE_CLAUDE_OUTPUT, or sleeps when FAKE_CLAUDE_OUTPUT is 'sleep'."""
import json
import os
import sys
import time

log = os.environ["FAKE_CLAUDE_LOG"]
with open(log, "a", encoding="utf-8") as fh:
    fh.write(json.dumps({"args": sys.argv[1:], "cwd": os.getcwd(), "cwd_entries": sorted(os.listdir(os.getcwd())),
                         "api_key_in_env": "ANTHROPIC_API_KEY" in os.environ, "stdin": sys.stdin.read()}) + "\n")
mode = os.environ["FAKE_CLAUDE_OUTPUT"]
if mode == "sleep":
    time.sleep(60)
else:
    with open(mode, encoding="utf-8") as fh:
        sys.stdout.write(fh.read())
