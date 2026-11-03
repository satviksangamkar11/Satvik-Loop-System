# P0 sandbox image for Satvik Loop production jobs (2026-11-02).
# Build once:   docker build -t satvik-pytest-sandbox:latest tools/sandbox
# The loop runs it with --network none, --read-only, --user 65534, --cap-drop ALL, no-new-privileges and CPU/memory/PID limits
# (scripts/source/sandbox_runner.py). Add the PROJECT'S OWN test dependencies below: the container has no network at test time.
FROM python:3.13-slim
# git: the merge queue runs the loop's own suite here, and its launcher and merge tests make throw-away git repos (2026-11-03)
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*
# pytest plus the loop's own test dependencies (fix_loop imports yaml and jsonschema), pinned
RUN pip install --no-cache-dir "pytest==9.1.1" "pyyaml==6.0.3" "jsonschema==4.26.0" \
 && rm -rf /root/.cache
USER 65534:65534
WORKDIR /work
