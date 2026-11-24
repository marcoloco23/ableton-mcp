"""A faster main thread for the commands: about 20 ms a call instead of 200.

Live runs a Remote Script's scheduled messages on its tick, every 100 ms, and the socket
thread only gets to run Python while the main thread does: every command cost two ticks
(200 ms measured on 24.11.2026, a read as much as a write). Live.Base.Timer calls back on
the main thread every few ms, so a timer runs the waiting commands, and gives the socket
thread a moment to read and answer. It stops after IDLE_S without a command; the next
command then arrives on Live's tick and starts it again. Messages with a delay (the
automation pass counts in ticks) keep Live's tick.
"""

from __future__ import absolute_import, print_function, unicode_literals

import collections
import time

INTERVAL_MS = 10
YIELD_S = 0.001
IDLE_S = 3.0


class Pump(object):
    def __init__(self, ctrl, make_timer, slow, clock=time.time, sleep=time.sleep):
        self._waiting = collections.deque()
        self._slow, self._clock, self._sleep, self._log = slow, clock, sleep, getattr(ctrl, "log_message", None)
        self._last = clock()
        self._running = True
        self._timer = make_timer(self.tick, INTERVAL_MS)
        self._timer.start()

    def schedule(self, delay, task, *a, **k):
        """Stands in for ControlSurface.schedule_message; called from the socket threads too."""
        if delay or a or k or not self._running:
            return self._slow(delay, task, *a, **k)
        self._waiting.append(task)

    def tick(self):
        """On the main thread, every INTERVAL_MS: run what waits, then let the socket threads run."""
        ran = False
        while self._waiting:
            task = self._waiting.popleft()
            ran = True
            try:
                task()
            except Exception as e:  # one command's failure never stops the timer
                if self._log:
                    self._log("AbletonMCP pump: " + str(e))
        now = self._clock()
        if ran:
            self._last = now
        elif now - self._last > IDLE_S:
            self._running = False  # from here commands go through Live's tick
            if not self._waiting:
                self._timer.stop()
                return
            self._running = True
        self._sleep(YIELD_S)

    def wake(self):
        """On the main thread, with each command: the timer runs (again)."""
        self._last = self._clock()
        if not self._running:
            self._running = True
            self._timer.start()


def install(ctrl):
    """Give ctrl a pump, once, and wake it. Nothing happens where Live offers no timer (tests, old versions)."""
    pump = getattr(ctrl, "_mcp_pump", None)
    if pump is None:
        try:
            from Live.Base import Timer
        except ImportError:
            return
        pump = Pump(ctrl, lambda fn, ms: Timer(callback=fn, interval=ms, repeat=True), ctrl.schedule_message)
        ctrl._mcp_pump = pump
        ctrl.schedule_message = pump.schedule
    pump.wake()
