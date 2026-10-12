import pytest

from crashproof.ids import IdGen


def test_same_seed_same_ids() -> None:
    a, b = IdGen(1), IdGen(1)
    assert [a.new("wf") for _ in range(5)] == [b.new("wf") for _ in range(5)]


def test_format_and_per_prefix_counters() -> None:
    gen = IdGen(1)
    assert gen.new("wf") == "wf-000001"
    assert gen.new("wf") == "wf-000002"
    assert gen.new("lbl") == "lbl-000001"


def test_ten_thousand_ids_unique() -> None:
    gen = IdGen(1)
    ids = {gen.new("wf") for _ in range(10_000)}
    assert len(ids) == 10_000


@pytest.mark.parametrize("prefix", ["", "WF", "a1", "a-b", "a b"])
def test_bad_prefix_raises(prefix: str) -> None:
    with pytest.raises(ValueError):
        IdGen(1).new(prefix)
