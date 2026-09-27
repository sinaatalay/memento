from datetime import timedelta

import pytest

from memento import state


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "HOME", tmp_path)


def test_travel_moves_and_resets_the_clock():
    state.travel("+3h")
    state.travel("+1d30m")
    assert state.offset() == timedelta(days=1, hours=3, minutes=30)
    state.travel("-1d")
    assert state.offset() == timedelta(hours=3, minutes=30)
    state.travel("reset")
    assert state.offset() == timedelta()


def test_travel_rejects_nonsense():
    with pytest.raises(ValueError):
        state.travel("+3 fortnights")


def test_travel_to_a_weekday(monkeypatch):
    from datetime import datetime

    from memento.api import TZ

    sunday = datetime(2026, 9, 27, 16, 0, tzinfo=TZ)
    real = datetime.now(TZ).replace(microsecond=0)
    state.set_offset(sunday - real)
    assert state.travel("mon 7:45").strftime("%a %H:%M %d") == "Mon 07:45 28"
    assert state.travel("sunday 22:45").strftime("%a %H:%M %d") == "Sun 22:45 04"
