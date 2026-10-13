from __future__ import annotations

from math import asin, cos, radians, sin, sqrt
from uuid import uuid4

from app.pricing import calculate_base_fare, calculate_discount
from app.schemas import (
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


class ServiceError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two GPS points, in kilometers."""
    earth_radius_km = 6371.0088
    d_lat = radians(lat2 - lat1)
    d_lon = radians(lon2 - lon1)
    a = sin(d_lat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(d_lon / 2) ** 2
    # Clamp against floating-point drift at the limits of asin's domain.
    return 2 * earth_radius_km * asin(sqrt(min(1.0, max(0.0, a))))


class RideHailingService:
    def __init__(self, store: InMemoryStore | None = None) -> None:
        self.store = store or InMemoryStore()

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

    def book_ride(self, payload: RideCreate) -> RideRecord:
        # Keep driver selection, availability mutation, coupon snapshot and ride
        # creation in one critical section. Two requests cannot claim one driver
        # concurrently in this process.
        with self.store.lock:
            if payload.user_id not in self.store.users:
                raise ServiceError(404, "User not found")

            coupon: Coupon | None = None
            if payload.coupon_code:
                coupon = self.store.coupons.get(payload.coupon_code)
                if coupon is None:
                    raise ServiceError(400, f"Coupon '{payload.coupon_code}' is invalid")

            eligible = []
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

            # Only Hatchback has the explicitly required no-extra-cost Sedan
            # upgrade path. The original requested type controls the fare.
            if not candidates and payload.requested_vehicle_type == VehicleType.HATCHBACK:
                candidates = [pair for pair in eligible if pair[1].vehicle_type == VehicleType.SEDAN]
                upgraded = bool(candidates)

            if not candidates:
                raise ServiceError(409, "No available driver of the requested type within radius")

            _, driver = min(candidates, key=lambda pair: (pair[0], pair[1].id))
            assigned_driver = driver.model_copy(update={"available": False})
            self.store.drivers[driver.id] = assigned_driver

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
                created_at=utc_now(),
                started_at=utc_now(),
            )
            self.store.rides[ride.id] = ride
            return ride

    def end_ride(self, ride_id: str, distance_km: float) -> tuple[RideRecord, FareBreakdown]:
        with self.store.lock:
            ride = self.store.rides.get(ride_id)
            if ride is None:
                raise ServiceError(404, "Ride not found")
            if ride.status != RideStatus.ONGOING:
                raise ServiceError(409, "Ride is already completed")

            base_fare = calculate_base_fare(distance_km, ride.pricing_vehicle_type)
            discount = calculate_discount(
                base_fare,
                ride.coupon_discount_type,
                ride.coupon_discount_value,
                ride.coupon_max_discount,
            )
            final_fare = round(max(0.0, base_fare - discount), 2)
            now = utc_now()
            completed = ride.model_copy(
                update={
                    "status": RideStatus.COMPLETED,
                    "ended_at": now,
                    "distance_km": round(distance_km, 3),
                    "base_fare": base_fare,
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
                coupon_code=completed.coupon_code,
                discount_amount=discount,
                final_fare=final_fare,
            )
            return completed, breakdown

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
