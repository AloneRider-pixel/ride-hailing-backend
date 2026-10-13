from __future__ import annotations

from dataclasses import dataclass
from math import floor
from typing import Protocol


class SurgePricingStrategy(Protocol):
    """Pluggable pricing contract that returns a multiplier >= 1.0."""

    name: str

    def multiplier(self, *, demand: int, available_supply: int) -> float:
        ...


@dataclass(frozen=True)
class DemandSupplySurgePricing:
    """Area demand/supply pricing with a capped, configurable tier schedule.

    Ratio tiers: <=1.0 => 1.0x, <=1.5 => 1.2x, <=2.0 => 1.5x,
    and >2.0 => 2.0x. Zero supply in the cell maps to the cap.
    """

    name: str = "demand_supply"
    area_cell_degrees: float = 0.05
    max_multiplier: float = 2.0

    def __post_init__(self) -> None:
        if self.area_cell_degrees <= 0:
            raise ValueError("area_cell_degrees must be positive")
        if self.max_multiplier < 1.0 or self.max_multiplier > 5.0:
            raise ValueError("max_multiplier must be between 1.0 and 5.0")

    def multiplier(self, *, demand: int, available_supply: int) -> float:
        if demand < 0 or available_supply < 0:
            raise ValueError("demand and available_supply cannot be negative")
        if demand == 0:
            return 1.0
        if available_supply == 0:
            return round(self.max_multiplier, 2)
        ratio = demand / available_supply
        if ratio <= 1.0:
            multiplier = 1.0
        elif ratio <= 1.5:
            multiplier = 1.2
        elif ratio <= 2.0:
            multiplier = 1.5
        else:
            multiplier = self.max_multiplier
        return round(min(max(multiplier, 1.0), self.max_multiplier), 2)

    def area_key(self, latitude: float, longitude: float) -> tuple[int, int]:
        """Approximate area cell at default granularity."""
        return (
            floor(latitude / self.area_cell_degrees),
            floor(longitude / self.area_cell_degrees),
        )


@dataclass(frozen=True)
class NoSurgePricing:
    """Pluggable opt-out strategy useful for tests/deployments without surge."""

    name: str = "disabled"

    def multiplier(self, *, demand: int, available_supply: int) -> float:
        if demand < 0 or available_supply < 0:
            raise ValueError("demand and available_supply cannot be negative")
        return 1.0
