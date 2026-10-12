import pytest

from crashproof.clock import Clock, Scheduler, SchedulingError


def make() -> tuple[Clock, Scheduler]:
    clock = Clock()
    return clock, Scheduler(clock)


def test_clock_starts_at_zero_and_advances() -> None:
    clock = Clock()
    assert clock.now == 0
    clock.advance_to(5)
    assert clock.now == 5


def test_clock_cannot_go_backwards() -> None:
    clock = Clock()
    clock.advance_to(5)
    with pytest.raises(SchedulingError):
        clock.advance_to(4)


def test_same_time_is_fifo() -> None:
    _, sched = make()
    order: list[int] = []
    for i in range(5):
        sched.schedule_at(10, lambda i=i: order.append(i))
    sched.run_until_idle(100)
    assert order == [0, 1, 2, 3, 4]


def test_earlier_vt_runs_first() -> None:
    _, sched = make()
    order: list[str] = []
    sched.schedule_at(20, lambda: order.append("late"))
    sched.schedule_at(5, lambda: order.append("early"))
    sched.run_until_idle(100)
    assert order == ["early", "late"]


def test_scheduling_in_the_past_raises() -> None:
    clock, sched = make()
    clock.advance_to(10)
    with pytest.raises(SchedulingError):
        sched.schedule_at(9, lambda: None)


def test_negative_delay_raises() -> None:
    _, sched = make()
    with pytest.raises(SchedulingError):
        sched.schedule_in(-1, lambda: None)


def test_clock_advances_to_event_time() -> None:
    clock, sched = make()
    seen: list[int] = []
    sched.schedule_at(7, lambda: seen.append(clock.now))
    assert sched.run_next() is True
    assert seen == [7]
    assert clock.now == 7


def test_action_can_schedule_new_event() -> None:
    clock, sched = make()
    seen: list[int] = []

    def first() -> None:
        sched.schedule_in(3, lambda: seen.append(clock.now))

    sched.schedule_at(4, first)
    assert sched.run_until_idle(10) == 2
    assert seen == [7]
    assert sched.pending == 0


def test_run_next_on_empty_returns_false() -> None:
    _, sched = make()
    assert sched.run_next() is False


def test_schedule_returns_increasing_seq() -> None:
    _, sched = make()
    a = sched.schedule_at(1, lambda: None)
    b = sched.schedule_at(1, lambda: None)
    assert b > a


def test_max_events_cap_raises() -> None:
    _, sched = make()

    def forever() -> None:
        sched.schedule_in(0, forever)

    sched.schedule_at(0, forever)
    with pytest.raises(SchedulingError):
        sched.run_until_idle(50)
