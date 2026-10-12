import pytest

from crashproof.rng import Rng


def draws(rng: Rng, n: int = 8) -> list[float]:
    return [rng.random() for _ in range(n)]


def test_same_seed_same_sequence() -> None:
    assert draws(Rng(1)) == draws(Rng(1))


def test_different_seeds_differ() -> None:
    assert draws(Rng(1)) != draws(Rng(2))


def test_fork_is_reproducible() -> None:
    assert draws(Rng(1).fork("a")) == draws(Rng(1).fork("a"))


def test_fork_same_name_twice_is_identical() -> None:
    rng = Rng(1)
    assert draws(rng.fork("x")) == draws(rng.fork("x"))


def test_fork_names_differ() -> None:
    rng = Rng(1)
    assert draws(rng.fork("a")) != draws(rng.fork("b"))


def test_fork_unaffected_by_parent_draws() -> None:
    fresh = Rng(1)
    used = Rng(1)
    draws(used, 100)
    assert draws(fresh.fork("a")) == draws(used.fork("a"))


def test_nested_fork_path_matters() -> None:
    rng = Rng(1)
    assert draws(rng.fork("a").fork("b")) != draws(rng.fork("b"))


def test_randint_and_choice_deterministic() -> None:
    a, b = Rng(3), Rng(3)
    assert [a.randint(0, 9) for _ in range(5)] == [b.randint(0, 9) for _ in range(5)]
    assert [a.choice("xyz") for _ in range(5)] == [b.choice("xyz") for _ in range(5)]


def test_chance_extremes() -> None:
    rng = Rng(1)
    assert not any(rng.chance(0.0) for _ in range(100))
    assert all(rng.chance(1.0) for _ in range(100))


@pytest.mark.parametrize("p", [-0.1, 1.1])
def test_chance_bad_probability_raises(p: float) -> None:
    with pytest.raises(ValueError):
        Rng(1).chance(p)
