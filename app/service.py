from __future__ import annotations

import os
from dataclasses import dataclass
from math import asin, cos, radians, sin, sqrt
from uuid import uuid4

from app.matching import (
    DriverMatchingStrategy,
    available_matching_strategies,
    create_matching_strategy,
)
from app.pricing import calculate_base_fare, calculate_discount
from app.schemas import (
    CancellationResponse,
    Coupon,
    CouponCreate,
    Driver,
    DriverCreate,
    FareBreakdown,
    LocationUpdate,
    RideCreate,
    RideRecord,
    RideStatus,
    User,
    UserCreate,
    VehicleType,
    utc_now,
)
from app.store import InMemoryStore
from app.surge import DemandSupplySurgePricing, SurgePricingStrategy


class ServiceError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


@dataclass(frozen=True)
class CancellationFeePolicy:
    """Default demo policy: free cancellation for 120 seconds, then INR 30."""

    free_cancellation_seconds: int = 120
    fee_after_grace: float = 30.0

    def __post_init__(self) -> None:
        if self.free_cancellation_seconds < 0:
            raise ValueError("free_cancellation_seconds cannot be negative")
        if self.fee_after_grace < 0:
            raise ValueError("fee_after_grace cannot be negative")

    def fee_for_elapsed_seconds(self, elapsed_seconds: float) -> float:
        if elapsed_seconds <= self.free_cancellation_seconds:
            return 0.0
        return round(self.fee_after_grace, 2)


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two GPS points, in kilometers."""
    earth_radius_km = 6371.0088
    d_lat = radians(lat2 - lat1)
    d_lon = radians(lon2 - lon1)
    a = sin(d_lat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(d_lon / 2) ** 2
    return 2 * earth_radius_km * asin(sqrt(min(1.0, max(0.0, a))))


class RideHailingService:
    def __init__(
        self,
        store: InMemoryStore | None = None,
        *,
        matching_strategy: str | DriverMatchingStrategy | None = None,
        surge_pricing_strategy: SurgePricingStrategy | None = None,
        cancellation_policy: CancellationFeePolicy | None = None,
    ) -> None:
        self.store = store or InMemoryStore()
        strategy_name = matching_strategy if isinstance(matching_strategy, str) else None
        if strategy_name is None and matching_strategy is None:
            strategy_name = os.getenv("RIDE_MATCHING_STRATEGY", "nearest")
        self.matching_strategy: DriverMatchingStrategy = (
            create_matching_strategy(strategy_name)
            if isinstance(matching_strategy, str) or matching_strategy is None
            else matching_strategy
        )
        self.surge_pricing_strategy = surge_pricing_strategy or DemandSupplySurgePricing()
        self.cancellation_policy = cancellation_policy or CancellationFeePolicy()

    def register_user(self, payload: UserCreate) -> User:
        user = User(id=str(uuid4()), name=payload.name)
        with self.store.lock:
            self.store.users[user.id] = user
        return user

    def register_driver(self, payload: DriverCreate) -> Driver:
        driver = Driver(
            id=str(uuid4()),
            name=payload.name,
            vehicle_type=payload.vehicle_type,
            latitude=payload.latitude,
            longitude=payload.longitude,
            rating=payload.rating,
        )
        with self.store.lock:
            self.store.drivers[driver.id] = driver
        return driver

    def update_driver_location(self, driver_id: str, payload: LocationUpdate) -> Driver:
        with self.store.lock:
            driver = self.store.drivers.get(driver_id)
            if driver is None:
                raise ServiceError(404, "Driver not found")
            updated = driver.model_copy(
                update={"latitude": payload.latitude, "longitude": payload.longitude}
            )
            self.store.drivers[driver_id] = updated
            return updated

    def add_coupon(self, payload: CouponCreate) -> Coupon:
        coupon = Coupon(
            code=payload.code,
            discount_type=payload.discount_type,
            value=payload.value,
            max_discount=payload.max_discount,
        )
        with self.store.lock:
            if coupon.code in self.store.coupons:
                raise ServiceError(409, f"Coupon '{coupon.code}' already exists")
            self.store.coupons[coupon.code] = coupon
        return coupon

    def delete_coupon(self, code: str) -> None:
        normalized = code.strip().upper()
        with self.store.lock:
            if normalized not in self.store.coupons:
                raise ServiceError(404, "Coupon not found")
            del self.store.coupons[normalized]

    def _surge_multiplier_for_area(self, latitude: float, longitude: float) -> float:
        """Estimate live demand/supply for the pickup's coarse geographic cell."""
        area_key = getattr(self.surge_pricing_strategy, "area_key", None)
        if area_key is None:
            area = (round(latitude, 2), round(longitude, 2))
        else:
            area = area_key(latitude, longitude)

        ongoing_demand = 0
        for ride in self.store.rides.values():
            if ride.status != RideStatus.ONGOING:
                continue
            ride_area = area_key(ride.pickup_latitude, ride.pickup_longitude) if area_key else (
                round(ride.pickup_latitude, 2), round(ride.pickup_longitude, 2)
            )
            if ride_area == area:
                ongoing_demand += 1

        available_supply = 0
        for driver in self.store.drivers.values():
            if not driver.available:
                continue
            driver_area = area_key(driver.latitude, driver.longitude) if area_key else (
                round(driver.latitude, 2), round(driver.longitude, 2)
            )
            if driver_area == area:
                available_supply += 1

        return self.surge_pricing_strategy.multiplier(
            demand=ongoing_demand + 1,
            available_supply=available_supply,
        )

    def book_ride(self, payload: RideCreate) -> RideRecord:
        # Driver selection, surge snapshot, coupon snapshot, availability mutation,
        # and ride creation form one critical section for this single process.
        with self.store.lock:
            if payload.user_id not in self.store.users:
                raise ServiceError(404, "User not found")

            coupon: Coupon | None = None
            if payload.coupon_code:
                coupon = self.store.coupons.get(payload.coupon_code)
                if coupon is None:
                    raise ServiceError(400, f"Coupon '{payload.coupon_code}' is invalid")

            eligible: list[tuple[float, Driver]] = []
            for driver in self.store.drivers.values():
                if not driver.available:
                    continue
                distance = haversine_km(
                    payload.pickup_latitude,
                    payload.pickup_longitude,
                    driver.latitude,
                    driver.longitude,
                )
                if distance <= payload.radius_km:
                    eligible.append((distance, driver))

            requested_matches = [
                pair for pair in eligible if pair[1].vehicle_type == payload.requested_vehicle_type
            ]
            upgraded = False
            candidates = requested_matches

            # Required no-extra-cost fallback: a Hatchback request may receive a
            # Sedan if no eligible Hatchback exists; fare uses requested car type.
            if not candidates and payload.requested_vehicle_type == VehicleType.HATCHBACK:
                candidates = [pair for pair in eligible if pair[1].vehicle_type == VehicleType.SEDAN]
                upgraded = bool(candidates)

            if not candidates:
                raise ServiceError(409, "No available driver of the requested type within radius")

            _, driver = self.matching_strategy.select(candidates)
            multiplier = self._surge_multiplier_for_area(
                payload.pickup_latitude, payload.pickup_longitude
            )
            self.store.drivers[driver.id] = driver.model_copy(update={"available": False})

            now = utc_now()
            ride = RideRecord(
                id=str(uuid4()),
                user_id=payload.user_id,
                driver_id=driver.id,
                requested_vehicle_type=payload.requested_vehicle_type,
                assigned_vehicle_type=driver.vehicle_type,
                pricing_vehicle_type=payload.requested_vehicle_type,
                upgraded_at_no_extra_cost=upgraded,
                pickup_latitude=payload.pickup_latitude,
                pickup_longitude=payload.pickup_longitude,
                coupon_code=coupon.code if coupon else None,
                coupon_discount_type=coupon.discount_type if coupon else None,
                coupon_discount_value=coupon.value if coupon else None,
                coupon_max_discount=coupon.max_discount if coupon else None,
                surge_multiplier=multiplier,
                created_at=now,
                started_at=now,
            )
            self.store.rides[ride.id] = ride
            return ride

    def end_ride(self, ride_id: str, distance_km: float) -> tuple[RideRecord, FareBreakdown]:
        with self.store.lock:
            ride = self.store.rides.get(ride_id)
            if ride is None:
                raise ServiceError(404, "Ride not found")
            if ride.status != RideStatus.ONGOING:
                raise ServiceError(409, f"Cannot end a ride with status '{ride.status.value}'")

            base_fare = calculate_base_fare(distance_km, ride.pricing_vehicle_type)
            surged_fare = round(base_fare * ride.surge_multiplier, 2)
            surge_amount = round(surged_fare - base_fare, 2)
            discount = calculate_discount(
                surged_fare,
                ride.coupon_discount_type,
                ride.coupon_discount_value,
                ride.coupon_max_discount,
            )
            final_fare = round(max(0.0, surged_fare - discount), 2)
            now = utc_now()
            completed = ride.model_copy(
                update={
                    "status": RideStatus.COMPLETED,
                    "ended_at": now,
                    "distance_km": round(distance_km, 3),
                    "base_fare": base_fare,
                    "surge_amount": surge_amount,
                    "surged_fare": surged_fare,
                    "discount_amount": discount,
                    "final_fare": final_fare,
                }
            )
            self.store.rides[ride.id] = completed

            driver = self.store.drivers[ride.driver_id]
            self.store.drivers[driver.id] = driver.model_copy(update={"available": True})

            breakdown = FareBreakdown(
                ride_id=completed.id,
                distance_km=completed.distance_km,
                vehicle_type_priced=completed.pricing_vehicle_type,
                base_fare=base_fare,
                surge_multiplier=ride.surge_multiplier,
                surge_amount=surge_amount,
                surged_fare=surged_fare,
                coupon_code=completed.coupon_code,
                discount_amount=discount,
                final_fare=final_fare,
            )
            return completed, breakdown

    def cancel_ride(self, ride_id: str) -> CancellationResponse:
        with self.store.lock:
            ride = self.store.rides.get(ride_id)
            if ride is None:
                raise ServiceError(404, "Ride not found")
            if ride.status != RideStatus.ONGOING:
                raise ServiceError(409, f"Cannot cancel a ride with status '{ride.status.value}'")

            now = utc_now()
            elapsed_seconds = max(0.0, (now - ride.created_at).total_seconds())
            fee = self.cancellation_policy.fee_for_elapsed_seconds(elapsed_seconds)
            cancelled = ride.model_copy(
                update={
                    "status": RideStatus.CANCELLED,
                    "cancelled_at": now,
                    "cancellation_fee": fee,
                }
            )
            self.store.rides[ride.id] = cancelled
            driver = self.store.drivers[ride.driver_id]
            self.store.drivers[driver.id] = driver.model_copy(update={"available": True})

            if fee == 0:
                message = (
                    f"Cancelled within the {self.cancellation_policy.free_cancellation_seconds}-second "
                    "free-cancellation window."
                )
            else:
                message = "Cancellation fee applies after the free-cancellation window."
            return CancellationResponse(
                ride_id=ride.id,
                status=RideStatus.CANCELLED,
                cancelled_at=now,
                cancellation_fee=fee,
                policy_message=message,
            )

    def get_user_rides(self, user_id: str, status: RideStatus | None = None) -> list[RideRecord]:
        with self.store.lock:
            if user_id not in self.store.users:
                raise ServiceError(404, "User not found")
            rides = [ride for ride in self.store.rides.values() if ride.user_id == user_id]
            if status is not None:
                rides = [ride for ride in rides if ride.status == status]
            return sorted(rides, key=lambda ride: ride.created_at, reverse=True)

    def get_driver_rides(self, driver_id: str, status: RideStatus | None = None) -> list[RideRecord]:
        with self.store.lock:
            if driver_id not in self.store.drivers:
                raise ServiceError(404, "Driver not found")
            rides = [ride for ride in self.store.rides.values() if ride.driver_id == driver_id]
            if status is not None:
                rides = [ride for ride in rides if ride.status == status]
            return sorted(rides, key=lambda ride: ride.created_at, reverse=True)

    def get_driver(self, driver_id: str) -> Driver:
        with self.store.lock:
            driver = self.store.drivers.get(driver_id)
            if driver is None:
                raise ServiceError(404, "Driver not found")
            return driver

    def get_runtime_config(self) -> dict[str, str | int | float | list[str]]:
        return {
            "matching_strategy": self.matching_strategy.name,
            "available_matching_strategies": available_matching_strategies(),
            "surge_pricing_strategy": self.surge_pricing_strategy.name,
            "cancellation_free_window_seconds": self.cancellation_policy.free_cancellation_seconds,
            "cancellation_fee_after_grace_inr": self.cancellation_policy.fee_after_grace,
        }
