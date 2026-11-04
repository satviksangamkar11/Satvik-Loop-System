"""I68: a process's start identity exists on Windows too (GetProcessTimes via ctypes), so a reused pid is a dead owner there as on Linux. The
Windows path is tested with a fake kernel32 (this machine cannot call Windows APIs); the Linux path is tested for real. No model call."""
import ctypes
import os
import subprocess
import sys
import types

import loop_home as lh
import pytest


class FakeKernel32:
    """Records the calls. `creation` is the FILETIME count GetProcessTimes reports; `handle` is what OpenProcess returns (0 = no such process)."""

    def __init__(self, handle=77, creation=133_000_000_000_000_000, times_ok=True):
        self.handle, self.creation, self.times_ok = handle, creation, times_ok
        self.opened, self.closed = [], []

    def OpenProcess(self, access, inherit, pid):
        self.opened.append((access, bool(inherit), pid))
        return self.handle

    def GetProcessTimes(self, handle, created, exited, kernel, user):
        assert handle == self.handle
        for ptr in (created, exited, kernel, user):
            assert isinstance(ptr, ctypes._Pointer)            # every out parameter is a real pointer
        created.contents.value = self.creation
        return 1 if self.times_ok else 0

    def CloseHandle(self, handle):
        self.closed.append(handle)
        return 1


def test_windows_start_is_the_creation_time_as_a_string_and_the_handle_is_closed():
    fake = FakeKernel32()
    assert lh._windows_process_start(4242, k32=fake) == str(fake.creation)
    assert fake.opened == [(lh.PROCESS_QUERY_LIMITED_INFORMATION, False, 4242)]
    assert fake.closed == [77]


def test_windows_start_is_none_for_a_pid_that_does_not_exist_and_nothing_is_closed():
    fake = FakeKernel32(handle=0)
    assert lh._windows_process_start(4000000, k32=fake) is None
    assert fake.closed == []


def test_windows_start_is_none_when_the_times_cannot_be_read_and_the_handle_is_still_closed():
    fake = FakeKernel32(times_ok=False)
    assert lh._windows_process_start(4242, k32=fake) is None
    assert fake.closed == [77]


def test_a_reused_pid_is_a_dead_owner_on_windows_identity(monkeypatch):
    """With the Windows identity in place, a live pid whose creation time differs from the recorded start is a dead owner, and the same
    creation time is the same owner."""
    fake = FakeKernel32(creation=555)
    monkeypatch.setattr(lh, "_win_kernel32", lambda: fake)
    monkeypatch.setattr(lh, "os", types.SimpleNamespace(name="nt"))     # process_start only reads os.name
    monkeypatch.setattr(lh, "pid_alive", lambda pid: True)
    assert lh.process_start(os.getpid()) == "555"
    assert lh.owner_alive(os.getpid(), "555") is True
    assert lh.owner_alive(os.getpid(), "554") is False          # the pid is alive, another process started under it


def test_linux_start_is_the_kernel_start_time_of_this_process():
    if not os.path.exists("/proc/self/stat"):
        pytest.skip("no /proc on this platform")
    start = lh.process_start(os.getpid())
    assert start is not None and start.isdigit()
    fields = open("/proc/self/stat", encoding="ascii").read().rsplit(")", 1)[-1].split()
    assert start == fields[19]


def test_linux_start_of_a_pid_that_does_not_exist_is_none():
    if not os.path.exists("/proc/self/stat"):
        pytest.skip("no /proc on this platform")
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    assert lh.process_start(proc.pid) is None


def test_a_linux_pid_reused_by_another_start_is_a_dead_owner():
    if lh.process_start(os.getpid()) is None:
        pytest.skip("this platform gives no process start identity")
    assert lh.owner_alive(os.getpid(), str(lh.process_start(os.getpid()))) is True
    assert lh.owner_alive(os.getpid(), "not-this-process") is False
