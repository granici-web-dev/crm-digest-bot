import pytest

from digest.mefi.client import RequestPacer
from factories import FakeTime


@pytest.fixture
def fake_time() -> FakeTime:
    return FakeTime()


async def test_pacer_waits_only_the_remaining_interval(fake_time: FakeTime) -> None:
    pacer = RequestPacer(1.2, sleep=fake_time.sleep, clock=fake_time.clock)

    await pacer.wait_turn()
    fake_time.now += 0.5
    await pacer.wait_turn()

    assert fake_time.sleeps == [pytest.approx(0.7)]


async def test_hold_delays_the_next_turn_of_any_caller(fake_time: FakeTime) -> None:
    pacer = RequestPacer(1.2, sleep=fake_time.sleep, clock=fake_time.clock)

    await pacer.wait_turn()
    pacer.hold(7)
    await pacer.wait_turn()
    await pacer.wait_turn()

    assert fake_time.sleeps == [7, pytest.approx(1.2)]


async def test_hold_never_shortens_the_interval(fake_time: FakeTime) -> None:
    pacer = RequestPacer(1.2, sleep=fake_time.sleep, clock=fake_time.clock)

    await pacer.wait_turn()
    pacer.hold(0.5)
    await pacer.wait_turn()

    assert fake_time.sleeps == [pytest.approx(1.2)]
