import re

_PREFIX = re.compile(r"[a-z]+")


class IdGen:
    """Counter-based id generator, one counter per prefix.

    Owned by the harness (runner), not by any runtime. Ids are unique within
    one generator and repeat across seeds by design: each seed gets a fresh
    generator, so the same seed always produces the same ids.
    """

    def __init__(self, seed: int) -> None:
        self._seed = seed
        self._counters: dict[str, int] = {}

    def new(self, prefix: str) -> str:
        if not _PREFIX.fullmatch(prefix):
            raise ValueError(f"invalid id prefix {prefix!r}: must match [a-z]+")
        n = self._counters.get(prefix, 0) + 1
        self._counters[prefix] = n
        return f"{prefix}-{n:06d}"
