"""Keeping the computer awake while on air."""
import subprocess

from station_server.awake import KeepAwake


def test_mac_holds_caffeinate_while_any_station_is_on(monkeypatch):
    started = []

    class Proc:
        def __init__(self, args, **kw):
            started.append(args)
            self.done = False

        def poll(self):
            return 0 if self.done else None

        def terminate(self):
            self.done = True

        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(subprocess, "Popen", Proc)
    awake = KeepAwake(platform="darwin")
    awake.hold()
    awake.hold()  # a second station
    assert len(started) == 1 and started[0][:2] == ["/usr/bin/caffeinate", "-i"]
    proc = awake._proc
    awake.release()
    assert awake.held and not proc.done
    awake.release()
    assert not awake.held and proc.done
    awake.release()  # more releases than holds do nothing
    assert not awake.held


def test_a_missing_tool_doesnt_stop_going_on_air(monkeypatch):
    def fails(*a, **kw):
        raise OSError("no caffeinate")

    monkeypatch.setattr(subprocess, "Popen", fails)
    awake = KeepAwake(platform="darwin")
    awake.hold()
    assert awake.held
    awake.release()
