from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Protocol

from app.schemas import DiscountType, VehicleType


@dataclass(frozen=True)
class VehiclePricing:
    minimum_fare: float
    first_two_km_rate: float
    next_three_km_rate: float
    beyond_five_km_rate: float


# Sedan uses the pricing example in the assignment. Hatchback rates are an
# explicitly documented assumption because the brief doesn't give exact rates.
VEHICLE_PRICING: dict[VehicleType, VehiclePricing] = {
    VehicleType.SEDAN: VehiclePricing(50.0, 10.0, 8.0, 5.0),
    VehicleType.HATCHBACK: VehiclePricing(40.0, 8.0, 6.0, 4.0),
}


class FarePricingStrategy(Protocol):
    """Pluggable contract for calculating a base fare before surge/coupons.

    To add a pricing policy, implement this protocol and inject it into
    `RideHailingService` or `create_app`; booking logic need not change.
    """

    name: str

    def calculate_base_fare(self, distance_km: float, vehicle_type: VehicleType) -> float:
        """Return a non-negative fare in INR before surge and coupon discount."""
        ...


@dataclass(frozen=True)
class ProgressiveTierPricing:
    """Progressive distance tiers, with rate cards supplied by vehicle type."""

    rate_cards: Mapping[VehicleType, VehiclePricing] = field(
        default_factory=lambda: dict(VEHICLE_PRICING)
    )
    name: str = "progressive_tiers"

    def calculate_base_fare(self, distance_km: float, vehicle_type: VehicleType) -> float:
        if distance_km < 0:
            raise ValueError("distance_km cannot be negative")
        try:
            rates = self.rate_cards[vehicle_type]
        except KeyError as exc:
            raise ValueError(f"No pricing configured for vehicle type: {vehicle_type}") from exc

        first_tier_distance = min(distance_km, 2.0)
        second_tier_distance = min(max(distance_km - 2.0, 0.0), 3.0)
        third_tier_distance = max(distance_km - 5.0, 0.0)
        metered_fare = (
            first_tier_distance * rates.first_two_km_rate
            + second_tier_distance * rates.next_three_km_rate
            + third_tier_distance * rates.beyond_five_km_rate
        )
        return round(max(rates.minimum_fare, metered_fare), 2)


DEFAULT_FARE_PRICING = ProgressiveTierPricing()


def calculate_base_fare(distance_km: float, vehicle_type: VehicleType) -> float:
    """Backwards-compatible helper using the default progressive tier policy."""
    return DEFAULT_FARE_PRICING.calculate_base_fare(distance_km, vehicle_type)


def calculate_discount(
    base_fare: float,
    discount_type: DiscountType | None,
    discount_value: float | None,
    max_discount: float | None,
) -> float:
    """Calculate a non-negative coupon discount, never exceeding the fare."""
    if discount_type is None or discount_value is None:
        return 0.0

    if discount_type == DiscountType.PERCENTAGE:
        discount = base_fare * discount_value / 100.0
        if max_discount is not None:
            discount = min(discount, max_discount)
    elif discount_type == DiscountType.FIXED:
        discount = discount_value
    else:
        raise ValueError(f"Unsupported discount type: {discount_type}")

    return round(min(max(discount, 0.0), base_fare), 2)
