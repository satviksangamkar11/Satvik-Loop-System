"""Test guard (patch 6, 2026-11-02): the Opus repair switch is OFF for every test, so no test can start the real Claude CLI. A test that needs the
repair step turns it on with its own fake (pieces/fake_claude.py or a scripted ask) and monkeypatch undoes it afterwards."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "source"))


@pytest.fixture(autouse=True)
def opus_switch_off(monkeypatch):
    import opus_repair
    monkeypatch.setattr(opus_repair, "OPUS_ENABLED", False)
