from __future__ import annotations

from dataclasses import dataclass

from app.schemas import DiscountType, VehicleType


@dataclass(frozen=True)
class VehiclePricing:
    minimum_fare: float
    first_two_km_rate: float
    next_three_km_rate: float
    beyond_five_km_rate: float


# Prices are deliberately configuration-driven. Sedan uses the example tiers in
# the assignment; Hatchback has a cheaper rate to satisfy per-vehicle pricing.
VEHICLE_PRICING: dict[VehicleType, VehiclePricing] = {
    VehicleType.SEDAN: VehiclePricing(
        minimum_fare=50.0,
        first_two_km_rate=10.0,
        next_three_km_rate=8.0,
        beyond_five_km_rate=5.0,
    ),
    VehicleType.HATCHBACK: VehiclePricing(
        minimum_fare=40.0,
        first_two_km_rate=8.0,
        next_three_km_rate=6.0,
        beyond_five_km_rate=4.0,
    ),
}


def calculate_base_fare(distance_km: float, vehicle_type: VehicleType) -> float:
    """Return the minimum-fare-clamped, progressive-tier fare in INR.

    The first 2 km use tier one, the next 3 km use tier two, and distance over
    5 km uses tier three. The minimum fare is applied before any coupon discount.
    """
    if distance_km < 0:
        raise ValueError("distance_km cannot be negative")

    rates = VEHICLE_PRICING[vehicle_type]
    first_tier_distance = min(distance_km, 2.0)
    second_tier_distance = min(max(distance_km - 2.0, 0.0), 3.0)
    third_tier_distance = max(distance_km - 5.0, 0.0)

    metered_fare = (
        first_tier_distance * rates.first_two_km_rate
        + second_tier_distance * rates.next_three_km_rate
        + third_tier_distance * rates.beyond_five_km_rate
    )
    return round(max(rates.minimum_fare, metered_fare), 2)


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
    else:  # Defensive guard if a new enum value is ever introduced.
        raise ValueError(f"Unsupported discount type: {discount_type}")

    return round(min(max(discount, 0.0), base_fare), 2)
