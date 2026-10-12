import hashlib
import random
from collections.abc import Sequence


class Rng:
    """Seeded random stream.

    Owned by the harness (runner), not by any runtime. A child stream from
    fork(name) depends only on (seed, path, name), never on how much the parent
    has drawn. Forking the same name twice returns an identical stream, so each
    consumer must use a distinct name.
    """

    def __init__(self, seed: int, path: str = "root") -> None:
        self._seed = seed
        self._path = path
        self._random = random.Random(seed)

    def random(self) -> float:
        return self._random.random()

    def randint(self, a: int, b: int) -> int:
        return self._random.randint(a, b)

    def choice[T](self, seq: Sequence[T]) -> T:
        return self._random.choice(seq)

    def chance(self, p: float) -> bool:
        if not 0.0 <= p <= 1.0:
            raise ValueError(f"probability {p} outside [0, 1]")
        return self._random.random() < p

    def fork(self, name: str) -> "Rng":
        child_path = f"{self._path}/{name}"
        digest = hashlib.sha256(f"{self._seed}|{child_path}".encode()).digest()
        return Rng(int.from_bytes(digest[:8], "big"), child_path)
