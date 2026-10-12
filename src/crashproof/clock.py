import heapq
from collections.abc import Callable


class SchedulingError(ValueError):
    """Raised when time would move backwards or the scheduler runs away."""


class Clock:
    """Virtual clock in integer seconds from simulation start.

    Owned by the harness (runner), not by any runtime. Runtimes receive it as a
    parameter so that all runtimes see the same time source.
    """

    def __init__(self) -> None:
        self._now = 0

    @property
    def now(self) -> int:
        return self._now

    def advance_to(self, vt: int) -> None:
        if vt < self._now:
            raise SchedulingError(f"cannot move clock back from {self._now} to {vt}")
        self._now = vt


class Scheduler:
    """Single-threaded discrete-event scheduler.

    Events run in (virtual_time, insertion_sequence) order, so ties are FIFO.
    Owned by the harness (runner), not by any runtime. Actions are zero-arg
    callables; callers bind their arguments with a closure or functools.partial.
    """

    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        self._queue: list[tuple[int, int, Callable[[], None]]] = []
        self._next_seq = 0

    @property
    def pending(self) -> int:
        return len(self._queue)

    def schedule_at(self, vt: int, action: Callable[[], None]) -> int:
        if vt < self._clock.now:
            raise SchedulingError(
                f"cannot schedule at {vt}, clock is at {self._clock.now}"
            )
        seq = self._next_seq
        self._next_seq += 1
        heapq.heappush(self._queue, (vt, seq, action))
        return seq

    def schedule_in(self, delay: int, action: Callable[[], None]) -> int:
        if delay < 0:
            raise SchedulingError(f"negative delay {delay}")
        return self.schedule_at(self._clock.now + delay, action)

    def run_next(self) -> bool:
        if not self._queue:
            return False
        vt, _, action = heapq.heappop(self._queue)
        self._clock.advance_to(vt)
        action()
        return True

    def run_until_idle(self, max_events: int) -> int:
        ran = 0
        while self._queue:
            if ran >= max_events:
                raise SchedulingError(
                    f"event cap {max_events} hit with events still pending"
                )
            self.run_next()
            ran += 1
        return ran
