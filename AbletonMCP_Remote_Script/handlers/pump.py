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
import threading
import time

INTERVAL_MS = 10
YIELD_S = 0.001
IDLE_S = 3.0
STALE_S = 9.0      # a command older than this has no one waiting (the socket side gives up after 10 s): never run it late


class Pump(object):
    def __init__(self, ctrl, make_timer, slow, clock=time.time, sleep=time.sleep):
        self._waiting = collections.deque()   # (when, task)
        self._lock = threading.Lock()
        self._slow, self._clock, self._sleep, self._log = slow, clock, sleep, getattr(ctrl, "log_message", None)
        self._last = clock()
        self._running = True
        self._timer = make_timer(self.tick, INTERVAL_MS)
        self._timer.start()

    def schedule(self, delay, task, *a, **k):
        """Stands in for ControlSurface.schedule_message; called from the socket threads too."""
        if delay or a or k:
            return self._slow(delay, task, *a, **k)
        with self._lock:  # the idle check below takes the same lock: a command can't slip in between its test and the stop
            if self._running:
                self._waiting.append((self._clock(), task))
                return None
        return self._slow(delay, task)

    def tick(self):
        """On the main thread, every INTERVAL_MS: run what waits, then let the socket threads run."""
        ran = False
        while True:
            with self._lock:
                if not self._waiting:
                    break
                when, task = self._waiting.popleft()
            ran = True
            if self._clock() - when > STALE_S:  # whoever asked has given up; an edit that lands later is worse than none
                if self._log:
                    self._log("AbletonMCP pump: dropped a command that waited too long")
                continue
            try:
                task()
            except Exception as e:  # one command's failure never stops the timer
                if self._log:
                    self._log("AbletonMCP pump: " + str(e))
        now = self._clock()
        if ran:
            self._last = now
        elif now - self._last > IDLE_S:
            with self._lock:
                if not self._waiting:
                    self._running = False  # from here commands go through Live's tick
            if not self._running:
                self._timer.stop()
                return
        self._sleep(YIELD_S)

    def wake(self):
        """On the main thread, with each command: the timer runs (again)."""
        self._last = self._clock()
        with self._lock:
            start = not self._running
            self._running = True
        if start:
            self._timer.start()

    def retire(self):
        """This pump is being replaced (the module was reloaded): stop its timer, hand its waiting commands on."""
        with self._lock:
            self._running = False
            left, self._waiting = self._waiting, collections.deque()
        self._timer.stop()
        return left


def install(ctrl):
    """Give ctrl a pump, once, and wake it. Nothing happens where Live offers no timer (tests, old versions)."""
    pump = getattr(ctrl, "_mcp_pump", None)
    if pump is not None and not isinstance(pump, Pump):  # a reload: the old class's pump goes
        slow = pump._slow
        getattr(pump, "retire", lambda: pump._timer.stop())()
        ctrl.schedule_message = slow
        pump = ctrl._mcp_pump = None
    if pump is None:
        try:
            from Live.Base import Timer
        except ImportError:
            return
        pump = Pump(ctrl, lambda fn, ms: Timer(callback=fn, interval=ms, repeat=True), ctrl.schedule_message)
        ctrl._mcp_pump = pump
        ctrl.schedule_message = pump.schedule
    pump.wake()
