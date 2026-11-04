"""Satvik Loop Control Center backend (P2): a local FastAPI server that wraps the engine in scripts/source/ and implements
docs/API_CONTRACT.md. Run it with `python -m uvicorn monitor.api:app --port 8765` from the repo root."""
import sys
from pathlib import Path

ENGINE = Path(__file__).resolve().parents[1] / "scripts" / "source"
if str(ENGINE) not in sys.path:               # the engine modules are imported by their bare names (event_log, fix_loop, ...)
    sys.path.insert(0, str(ENGINE))
