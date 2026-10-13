from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class VehicleType(str, Enum):
    SEDAN = "sedan"
    HATCHBACK = "hatchback"


class RideStatus(str, Enum):
    ONGOING = "ongoing"
    COMPLETED = "completed"


class DiscountType(str, Enum):
    PERCENTAGE = "percentage"
    FIXED = "fixed"


class APIModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, use_enum_values=False)


class UserCreate(APIModel):
    name: str = Field(min_length=1, max_length=100)

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("name must not be blank")
        return value


class User(APIModel):
    id: str
    name: str
    created_at: datetime = Field(default_factory=utc_now)


class DriverCreate(APIModel):
    name: str = Field(min_length=1, max_length=100)
    vehicle_type: VehicleType
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("name must not be blank")
        return value


class Driver(APIModel):
    id: str
    name: str
    vehicle_type: VehicleType
    latitude: float
    longitude: float
    available: bool = True
    created_at: datetime = Field(default_factory=utc_now)


class LocationUpdate(APIModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)


class CouponCreate(APIModel):
    code: str = Field(min_length=3, max_length=32, pattern=r"^[A-Za-z0-9_-]+$")
    discount_type: DiscountType
    value: float = Field(gt=0)
    max_discount: float | None = Field(default=None, gt=0)

    @field_validator("code")
    @classmethod
    def normalize_code(cls, value: str) -> str:
        return value.strip().upper()

    @model_validator(mode="after")
    def validate_discount(self) -> "CouponCreate":
        if self.discount_type == DiscountType.PERCENTAGE and self.value > 100:
            raise ValueError("percentage coupon value must be between 0 and 100")
        if self.discount_type == DiscountType.FIXED and self.max_discount is not None:
            raise ValueError("max_discount is only valid for percentage coupons")
        return self


class Coupon(APIModel):
    code: str
    discount_type: DiscountType
    value: float
    max_discount: float | None = None
    created_at: datetime = Field(default_factory=utc_now)


class RideCreate(APIModel):
    user_id: str = Field(min_length=1)
    pickup_latitude: float = Field(ge=-90, le=90)
    pickup_longitude: float = Field(ge=-180, le=180)
    requested_vehicle_type: VehicleType
    radius_km: float = Field(default=5.0, gt=0, le=100)
    coupon_code: str | None = Field(default=None, min_length=3, max_length=32)

    @field_validator("coupon_code")
    @classmethod
    def normalize_coupon_code(cls, value: str | None) -> str | None:
        return value.strip().upper() if value else None


class EndRideRequest(APIModel):
    # The caller supplies the measured/route distance; straight-line GPS distance is
    # deliberately not presented as actual driving distance.
    distance_km: float = Field(gt=0, le=10_000)


class RideRecord(APIModel):
    id: str
    user_id: str
    driver_id: str
    requested_vehicle_type: VehicleType
    assigned_vehicle_type: VehicleType
    # Pricing follows the requested type during the no-extra-cost upgrade case.
    pricing_vehicle_type: VehicleType
    upgraded_at_no_extra_cost: bool = False
    pickup_latitude: float
    pickup_longitude: float
    status: RideStatus = RideStatus.ONGOING
    coupon_code: str | None = None
    # Coupon terms are snapshotted at booking so later deletion/editing cannot
    # change an already-booked ride's fare.
    coupon_discount_type: DiscountType | None = None
    coupon_discount_value: float | None = None
    coupon_max_discount: float | None = None
    created_at: datetime = Field(default_factory=utc_now)
    started_at: datetime = Field(default_factory=utc_now)
    ended_at: datetime | None = None
    distance_km: float | None = None
    base_fare: float | None = None
    discount_amount: float | None = None
    final_fare: float | None = None


class RideResponse(APIModel):
    id: str
    user_id: str
    driver_id: str
    requested_vehicle_type: VehicleType
    assigned_vehicle_type: VehicleType
    pricing_vehicle_type: VehicleType
    upgraded_at_no_extra_cost: bool
    pickup_latitude: float
    pickup_longitude: float
    status: RideStatus
    coupon_code: str | None
    created_at: datetime
    started_at: datetime
    ended_at: datetime | None
    distance_km: float | None
    base_fare: float | None
    discount_amount: float | None
    final_fare: float | None


class FareBreakdown(APIModel):
    ride_id: str
    distance_km: float
    vehicle_type_priced: VehicleType
    base_fare: float
    coupon_code: str | None
    discount_amount: float
    final_fare: float
    currency: Literal["INR"] = "INR"
