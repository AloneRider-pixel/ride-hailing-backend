import pytest

from app.pricing import calculate_base_fare, calculate_discount
from app.schemas import DiscountType, VehicleType


def test_sedan_minimum_fare_applies_to_short_trip() -> None:
    assert calculate_base_fare(1.0, VehicleType.SEDAN) == 50.0


def test_sedan_progressive_tiers_are_applied() -> None:
    # 2 km * 10 + 3 km * 8 + 3 km * 5 = 59
    assert calculate_base_fare(8.0, VehicleType.SEDAN) == 59.0


def test_hatchback_has_a_different_rate_card() -> None:
    # 2 km * 8 + 3 km * 6 + 3 km * 4 = 46
    assert calculate_base_fare(8.0, VehicleType.HATCHBACK) == 46.0


def test_percentage_coupon_respects_cap() -> None:
    assert calculate_discount(100, DiscountType.PERCENTAGE, 20, 10) == 10.0


def test_fixed_coupon_never_exceeds_fare() -> None:
    assert calculate_discount(40, DiscountType.FIXED, 60, None) == 40.0


def test_negative_distance_is_rejected() -> None:
    with pytest.raises(ValueError):
        calculate_base_fare(-1, VehicleType.SEDAN)
