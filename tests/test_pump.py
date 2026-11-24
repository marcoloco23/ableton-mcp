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
