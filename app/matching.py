from __future__ import annotations

from typing import Protocol, Sequence

from app.schemas import Driver


Candidate = tuple[float, Driver]  # distance in km, driver record


class DriverMatchingStrategy(Protocol):
    """Strategy contract; booking logic is independent of selection policy."""

    name: str

    def select(self, candidates: Sequence[Candidate]) -> Candidate:
        """Choose one eligible driver; candidates is guaranteed non-empty."""
        ...


class NearestDriverStrategy:
    name = "nearest"

    def select(self, candidates: Sequence[Candidate]) -> Candidate:
        return min(candidates, key=lambda item: (item[0], item[1].id))


class HighestRatedDriverStrategy:
    name = "highest_rated"

    def select(self, candidates: Sequence[Candidate]) -> Candidate:
        # Rating is the primary key; nearest distance and stable ID break ties.
        return min(candidates, key=lambda item: (-item[1].rating, item[0], item[1].id))


STRATEGIES: dict[str, type[DriverMatchingStrategy]] = {
    NearestDriverStrategy.name: NearestDriverStrategy,
    HighestRatedDriverStrategy.name: HighestRatedDriverStrategy,
}


def create_matching_strategy(name: str | None = None) -> DriverMatchingStrategy:
    normalized = (name or "nearest").strip().lower()
    try:
        return STRATEGIES[normalized]()
    except KeyError as exc:
        supported = ", ".join(sorted(STRATEGIES))
        raise ValueError(f"Unknown matching strategy {normalized!r}; choose one of: {supported}") from exc


def available_matching_strategies() -> list[str]:
    return sorted(STRATEGIES)
