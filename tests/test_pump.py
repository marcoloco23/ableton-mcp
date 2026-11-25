"""The pump: commands without waiting for Live's 100 ms tick."""
import fake_live  # noqa: F401  (puts handlers on the path)
from handlers import pump


class Timer:
    def __init__(self, fn, ms):
        self.fn, self.ms, self.on = fn, ms, False

    def start(self):
        self.on = True

    def stop(self):
        self.on = False


class Rig:
    def __init__(self):
        self.now, self.slow, self.logged = 0.0, [], []
        self.pump = pump.Pump(self, self.make, lambda d, t, *a: self.slow.append((d, t)), lambda: self.now, lambda s: None)

    def make(self, fn, ms):
        self.timer = Timer(fn, ms)
        return self.timer

    def log_message(self, m):
        self.logged.append(m)


def test_a_command_runs_on_the_next_timer_call_and_a_delayed_message_keeps_lives_tick():
    r, done = Rig(), []
    r.pump.schedule(0, lambda: done.append(1))
    r.pump.schedule(1, lambda: done.append(2))
    r.timer.fn()
    assert done == [1] and [d for d, _ in r.slow] == [1]


def test_a_failing_command_is_logged_and_the_next_one_still_runs():
    r, done = Rig(), []
    r.pump.schedule(0, lambda: 1 / 0)
    r.pump.schedule(0, lambda: done.append(1))
    r.timer.fn()
    assert done == [1] and "division" in r.logged[0] and r.timer.on


def test_idle_stops_the_timer_and_commands_then_go_through_lives_tick_until_it_is_woken():
    r = Rig()
    r.now = pump.IDLE_S + 1
    r.timer.fn()
    assert not r.timer.on
    r.pump.schedule(0, "task")
    assert r.slow == [(0, "task")]
    r.pump.wake()
    assert r.timer.on
    done = []
    r.pump.schedule(0, lambda: done.append(1))
    r.timer.fn()
    assert done == [1]


def test_install_does_nothing_without_lives_timer():
    class Ctrl:
        pass

    c = Ctrl()
    pump.install(c)
    assert not hasattr(c, "_mcp_pump")


def test_a_command_never_gets_lost_at_the_idle_moment():
    """25.11.2026: an automate call timed out; it had been queued in the instant the timer went idle."""
    r, done = Rig(), []
    real = r.pump._timer.stop

    def stop_and_race():  # a command arriving while the timer stops must go through Live's tick, not be left behind
        r.pump.schedule(0, lambda: done.append("late"))
        real()

    r.timer.stop = stop_and_race
    r.now = pump.IDLE_S + 1
    r.timer.fn()
    assert r.slow and not r.pump._waiting


def test_a_command_that_waited_too_long_is_dropped_not_run_late():
    r, done = Rig(), []
    r.pump.schedule(0, lambda: done.append(1))
    r.now = pump.STALE_S + 1
    r.timer.fn()
    assert done == [] and "dropped" in r.logged[0]


def test_a_reload_replaces_the_old_pump_and_stops_its_timer(monkeypatch):
    class Old:
        stopped = False

        def __init__(self):
            self._slow = lambda *a: None
            self._timer = type("T", (), {"stop": lambda s: setattr(Old, "stopped", True)})()

    class Ctrl:
        log_message = staticmethod(lambda m: None)

        def schedule_message(self, *a):
            pass

    import sys
    import types
    live = types.ModuleType("Live.Base")
    live.Timer = lambda callback, interval, repeat: Timer(callback, interval)
    monkeypatch.setitem(sys.modules, "Live.Base", live)
    monkeypatch.setattr(sys.modules["Live"], "Base", live, raising=False)
    c = Ctrl()
    c._mcp_pump = Old()
    pump.install(c)
    assert Old.stopped and isinstance(c._mcp_pump, pump.Pump) and c.schedule_message == c._mcp_pump.schedule
