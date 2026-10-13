from __future__ import annotations

from datetime import timedelta

from fastapi.testclient import TestClient

from app.main import create_app
from app.schemas import RideStatus, utc_now
from app.store import InMemoryStore
from app.surge import DemandSupplySurgePricing, NoSurgePricing


PICKUP = {"pickup_latitude": 12.9716, "pickup_longitude": 77.5946}


def setup_client(*, strategy: str = "nearest", surge=None) -> TestClient:
    return TestClient(create_app(InMemoryStore(), matching_strategy=strategy, surge_pricing_strategy=surge))


def user_id(client: TestClient, name: str) -> str:
    response = client.post("/users", json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def driver_id(
    client: TestClient,
    name: str,
    *,
    rating: float = 5.0,
    lat: float = 12.9716,
    lon: float = 77.5946,
) -> str:
    response = client.post(
        "/drivers",
        json={
            "name": name,
            "vehicle_type": "sedan",
            "latitude": lat,
            "longitude": lon,
            "rating": rating,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def book(client: TestClient, uid: str) -> dict:
    response = client.post(
        "/rides",
        json={"user_id": uid, "requested_vehicle_type": "sedan", "radius_km": 5, **PICKUP},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_highest_rated_matching_is_configurable() -> None:
    client = setup_client(strategy="highest_rated", surge=NoSurgePricing())
    uid = user_id(client, "customer")
    near = driver_id(client, "near-lower-rating", rating=4.0, lat=12.9720, lon=77.5946)
    high = driver_id(client, "far-higher-rating", rating=4.9, lat=12.9800, lon=77.5946)

    ride = book(client, uid)
    assert ride["driver_id"] == high
    assert ride["driver_id"] != near
    assert client.get("/system/config").json()["matching_strategy"] == "highest_rated"


def test_nearest_matching_remains_selectable() -> None:
    client = setup_client(strategy="nearest", surge=NoSurgePricing())
    uid = user_id(client, "customer")
    near = driver_id(client, "near-high-rating", rating=4.0, lat=12.9720, lon=77.5946)
    driver_id(client, "far-high-rating", rating=5.0, lat=12.9800, lon=77.5946)
    ride = book(client, uid)
    assert ride["driver_id"] == near


def test_demand_supply_surge_is_snapshotted_and_applied_to_fare() -> None:
    client = setup_client(strategy="nearest", surge=DemandSupplySurgePricing())
    users = [user_id(client, f"customer-{index}") for index in range(3)]
    for index in range(3):
        driver_id(client, f"driver-{index}")

    ride_1 = book(client, users[0])
    ride_2 = book(client, users[1])
    ride_3 = book(client, users[2])
    assert ride_1["surge_multiplier"] == 1.0
    assert ride_2["surge_multiplier"] == 1.0
    assert ride_3["surge_multiplier"] == 2.0

    response = client.post(f"/rides/{ride_3['id']}/end", json={"distance_km": 8})
    assert response.status_code == 200, response.text
    fare = response.json()
    assert fare["base_fare"] == 59.0
    assert fare["surge_multiplier"] == 2.0
    assert fare["surge_amount"] == 59.0
    assert fare["surged_fare"] == 118.0
    assert fare["final_fare"] == 118.0


def test_surge_tiers_respond_to_demand_supply_pressure() -> None:
    policy = DemandSupplySurgePricing()
    assert policy.multiplier(demand=1, available_supply=4) == 1.0
    assert policy.multiplier(demand=3, available_supply=2) == 1.2
    assert policy.multiplier(demand=2, available_supply=1) == 1.5
    assert policy.multiplier(demand=3, available_supply=1) == 2.0
    assert policy.multiplier(demand=1, available_supply=0) == 2.0
    assert policy.multiplier(demand=0, available_supply=0) == 1.0


def test_free_cancellation_releases_driver_and_records_history() -> None:
    client = setup_client(surge=NoSurgePricing())
    uid = user_id(client, "customer")
    did = driver_id(client, "driver")
    ride = book(client, uid)

    response = client.post(f"/rides/{ride['id']}/cancel")
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["status"] == "cancelled"
    assert result["cancellation_fee"] == 0.0
    assert client.get(f"/drivers/{did}").json()["available"] is True
    assert len(client.get(f"/users/{uid}/rides?status=cancelled").json()) == 1
    assert client.post(f"/rides/{ride['id']}/end", json={"distance_km": 3}).status_code == 409


def test_late_cancellation_fee_applies_and_driver_becomes_available() -> None:
    client = setup_client(surge=NoSurgePricing())
    uid = user_id(client, "customer")
    did = driver_id(client, "driver")
    ride = book(client, uid)
    service = client.app.state.ride_service
    stored = service.store.rides[ride["id"]]
    service.store.rides[ride["id"]] = stored.model_copy(
        update={"created_at": utc_now() - timedelta(minutes=10)}
    )

    response = client.post(f"/rides/{ride['id']}/cancel")
    assert response.status_code == 200, response.text
    assert response.json()["cancellation_fee"] == 30.0
    assert client.get(f"/drivers/{did}").json()["available"] is True


def test_cancel_cannot_be_applied_twice_and_cancelled_is_terminal() -> None:
    client = setup_client(surge=NoSurgePricing())
    uid = user_id(client, "customer")
    driver_id(client, "driver")
    ride = book(client, uid)
    assert client.post(f"/rides/{ride['id']}/cancel").status_code == 200
    assert client.post(f"/rides/{ride['id']}/cancel").status_code == 409
    assert client.post(f"/rides/{ride['id']}/end", json={"distance_km": 3}).status_code == 409


def test_cannot_cancel_completed_ride() -> None:
    client = setup_client(surge=NoSurgePricing())
    uid = user_id(client, "customer")
    driver_id(client, "driver")
    ride = book(client, uid)
    assert client.post(f"/rides/{ride['id']}/end", json={"distance_km": 3}).status_code == 200
    assert client.post(f"/rides/{ride['id']}/cancel").status_code == 409


def test_environment_variable_switches_matching_strategy(monkeypatch) -> None:
    monkeypatch.setenv("RIDE_MATCHING_STRATEGY", "highest_rated")
    client = TestClient(create_app(InMemoryStore(), surge_pricing_strategy=NoSurgePricing()))
    assert client.get("/system/config").json()["matching_strategy"] == "highest_rated"


def test_driver_location_can_be_updated() -> None:
    client = setup_client(surge=NoSurgePricing())
    did = driver_id(client, "driver")
    response = client.patch(
        f"/drivers/{did}/location", json={"latitude": 13.0, "longitude": 77.7}
    )
    assert response.status_code == 200
    assert response.json()["latitude"] == 13.0
    assert response.json()["longitude"] == 77.7


def test_invalid_matching_strategy_fails_fast() -> None:
    import pytest

    with pytest.raises(ValueError, match="Unknown matching strategy"):
        setup_client(strategy="not-a-strategy")


def test_cancelled_status_is_an_enum_member() -> None:
    assert RideStatus.CANCELLED.value == "cancelled"



def test_custom_fare_strategy_is_injected_without_changing_booking_logic() -> None:
    from app.schemas import VehicleType

    class LaunchPromoPricing:
        name = "launch_promo"

        def calculate_base_fare(self, distance_km: float, vehicle_type: VehicleType) -> float:
            assert distance_km > 0
            return 42.0

    client = TestClient(
        create_app(
            InMemoryStore(),
            matching_strategy="nearest",
            surge_pricing_strategy=NoSurgePricing(),
            fare_pricing_strategy=LaunchPromoPricing(),
        )
    )
    uid = user_id(client, "customer")
    driver_id(client, "driver")
    ride = book(client, uid)

    response = client.post(f"/rides/{ride['id']}/end", json={"distance_km": 8})
    assert response.status_code == 200, response.text
    assert response.json()["base_fare"] == 42.0
    assert response.json()["final_fare"] == 42.0
    assert client.get("/system/config").json()["fare_pricing_strategy"] == "launch_promo"
