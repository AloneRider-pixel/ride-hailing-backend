from __future__ import annotations

from typing import Annotated

from fastapi import FastAPI, Query, status
from fastapi.responses import JSONResponse

from app.matching import DriverMatchingStrategy
from app.pricing import FarePricingStrategy
from app.schemas import (
    CancellationResponse,
    Coupon,
    CouponCreate,
    Driver,
    DriverCreate,
    EndRideRequest,
    FareBreakdown,
    LocationUpdate,
    RideCreate,
    RideRecord,
    RideResponse,
    RideStatus,
    User,
    UserCreate,
)
from app.service import CancellationFeePolicy, RideHailingService, ServiceError
from app.store import InMemoryStore
from app.surge import SurgePricingStrategy


def create_app(
    store: InMemoryStore | None = None,
    *,
    matching_strategy: str | DriverMatchingStrategy | None = None,
    surge_pricing_strategy: SurgePricingStrategy | None = None,
    fare_pricing_strategy: FarePricingStrategy | None = None,
    cancellation_policy: CancellationFeePolicy | None = None,
) -> FastAPI:
    api = FastAPI(
        title="Ride-Hailing Service Backend",
        description=(
            "A compact ride-hailing backend for the Plazza AI-assisted coding assignment. "
            "Uses in-memory storage; data resets when the process restarts."
        ),
        version="1.1.0",
    )
    api.state.ride_service = RideHailingService(
        store,
        matching_strategy=matching_strategy,
        surge_pricing_strategy=surge_pricing_strategy,
        fare_pricing_strategy=fare_pricing_strategy,
        cancellation_policy=cancellation_policy,
    )

    @api.exception_handler(ServiceError)
    async def service_error_handler(_, exc: ServiceError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    def service() -> RideHailingService:
        return api.state.ride_service

    @api.get("/health", tags=["health"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @api.get("/system/config", tags=["system"], summary="Show the active configurable policies")
    def get_system_config() -> dict[str, str | int | float | list[str]]:
        return service().get_runtime_config()

    @api.post("/users", response_model=User, status_code=status.HTTP_201_CREATED, tags=["users"])
    def register_user(payload: UserCreate) -> User:
        return service().register_user(payload)

    @api.post("/drivers", response_model=Driver, status_code=status.HTTP_201_CREATED, tags=["drivers"])
    def register_driver(payload: DriverCreate) -> Driver:
        return service().register_driver(payload)

    @api.patch("/drivers/{driver_id}/location", response_model=Driver, tags=["drivers"])
    def update_driver_location(driver_id: str, payload: LocationUpdate) -> Driver:
        return service().update_driver_location(driver_id, payload)

    @api.get("/drivers/{driver_id}", response_model=Driver, tags=["drivers"])
    def get_driver(driver_id: str) -> Driver:
        return service().get_driver(driver_id)

    @api.post("/coupons", response_model=Coupon, status_code=status.HTTP_201_CREATED, tags=["coupons"])
    def add_coupon(payload: CouponCreate) -> Coupon:
        return service().add_coupon(payload)

    @api.delete("/coupons/{code}", status_code=status.HTTP_204_NO_CONTENT, tags=["coupons"])
    def delete_coupon(code: str) -> None:
        service().delete_coupon(code)

    @api.post(
        "/rides",
        response_model=RideResponse,
        status_code=status.HTTP_201_CREATED,
        tags=["rides"],
        summary="Book a ride with the configured matching strategy",
    )
    def book_ride(payload: RideCreate) -> RideRecord:
        return service().book_ride(payload)

    @api.post("/rides/{ride_id}/end", response_model=FareBreakdown, tags=["rides"])
    def end_ride(ride_id: str, payload: EndRideRequest) -> FareBreakdown:
        _, breakdown = service().end_ride(ride_id, payload.distance_km)
        return breakdown

    @api.post("/rides/{ride_id}/cancel", response_model=CancellationResponse, tags=["rides"])
    def cancel_ride(ride_id: str) -> CancellationResponse:
        return service().cancel_ride(ride_id)

    @api.get("/users/{user_id}/rides", response_model=list[RideResponse], tags=["history"])
    def user_ride_history(
        user_id: str,
        ride_status: Annotated[RideStatus | None, Query(alias="status")] = None,
    ) -> list[RideRecord]:
        return service().get_user_rides(user_id, ride_status)

    @api.get("/drivers/{driver_id}/rides", response_model=list[RideResponse], tags=["history"])
    def driver_ride_history(
        driver_id: str,
        ride_status: Annotated[RideStatus | None, Query(alias="status")] = None,
    ) -> list[RideRecord]:
        return service().get_driver_rides(driver_id, ride_status)

    return api


app = create_app()
