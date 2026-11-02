# P0 sandbox image for Satvik Loop production jobs (2026-11-02).
# Build once:   docker build -t satvik-pytest-sandbox:latest tools/sandbox
# The loop runs it with --network none, --read-only, --user 65534, --cap-drop ALL, no-new-privileges and CPU/memory/PID limits
# (scripts/source/sandbox_runner.py). Add the PROJECT'S OWN test dependencies below: the container has no network at test time.
FROM python:3.13-slim
RUN pip install --no-cache-dir "pytest==9.1.1" \
 && rm -rf /root/.cache
# project test dependencies (pin them), for example:
# RUN pip install --no-cache-dir "pyyaml==6.0.3" "jsonschema==4.25.1"
USER 65534:65534
WORKDIR /work
