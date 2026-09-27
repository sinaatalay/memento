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
