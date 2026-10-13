from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient

from app.main import create_app
from app.store import InMemoryStore


def build_client() -> TestClient:
    return TestClient(create_app(InMemoryStore()))


def register_user(client: TestClient) -> str:
    response = client.post("/users", json={"name": "Test User"})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def register_driver(client: TestClient, name: str, vehicle_type: str, lat: float = 12.9716, lon: float = 77.5946) -> str:
    response = client.post(
        "/drivers",
        json={"name": name, "vehicle_type": vehicle_type, "latitude": lat, "longitude": lon},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def book(client: TestClient, user_id: str, vehicle_type: str = "sedan", coupon_code: str | None = None) -> dict:
    payload = {
        "user_id": user_id,
        "pickup_latitude": 12.9716,
        "pickup_longitude": 77.5946,
        "requested_vehicle_type": vehicle_type,
        "radius_km": 5,
    }
    if coupon_code:
        payload["coupon_code"] = coupon_code
    response = client.post("/rides", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def test_booking_end_ride_and_history() -> None:
    client = build_client()
    user_id = register_user(client)
    driver_id = register_driver(client, "D1", "sedan")

    ride = book(client, user_id)
    assert ride["status"] == "ongoing"
    assert ride["driver_id"] == driver_id
    assert client.get(f"/drivers/{driver_id}").json()["available"] is False

    response = client.post(f"/rides/{ride['id']}/end", json={"distance_km": 8})
    assert response.status_code == 200
    assert response.json()["base_fare"] == 59.0
    assert response.json()["final_fare"] == 59.0
    assert client.get(f"/drivers/{driver_id}").json()["available"] is True
    assert len(client.get(f"/users/{user_id}/rides?status=completed").json()) == 1
    assert len(client.get(f"/drivers/{driver_id}/rides?status=ongoing").json()) == 0


def test_hatchback_request_upgrades_to_sedan_without_extra_cost() -> None:
    client = build_client()
    user_id = register_user(client)
    register_driver(client, "Sedan Driver", "sedan")

    ride = book(client, user_id, vehicle_type="hatchback")
    assert ride["requested_vehicle_type"] == "hatchback"
    assert ride["assigned_vehicle_type"] == "sedan"
    assert ride["pricing_vehicle_type"] == "hatchback"
    assert ride["upgraded_at_no_extra_cost"] is True

    fare = client.post(f"/rides/{ride['id']}/end", json={"distance_km": 8}).json()
    assert fare["base_fare"] == 46.0  # hatchback rate, not sedan rate


def test_no_driver_in_radius_returns_conflict() -> None:
    client = build_client()
    user_id = register_user(client)
    register_driver(client, "Far Driver", "sedan", lat=28.6139, lon=77.2090)
    response = client.post(
        "/rides",
        json={
            "user_id": user_id,
            "pickup_latitude": 12.9716,
            "pickup_longitude": 77.5946,
            "requested_vehicle_type": "sedan",
            "radius_km": 1,
        },
    )
    assert response.status_code == 409


def test_coupon_applies_and_coupon_delete_does_not_change_booked_ride() -> None:
    client = build_client()
    user_id = register_user(client)
    register_driver(client, "D1", "sedan")
    coupon_response = client.post(
        "/coupons",
        json={"code": "SAVE10", "discount_type": "percentage", "value": 10, "max_discount": 20},
    )
    assert coupon_response.status_code == 201

    ride = book(client, user_id, coupon_code="save10")
    assert ride["coupon_code"] == "SAVE10"
    assert client.delete("/coupons/SAVE10").status_code == 204

    fare = client.post(f"/rides/{ride['id']}/end", json={"distance_km": 8}).json()
    assert fare["base_fare"] == 59.0
    assert fare["discount_amount"] == 5.9
    assert fare["final_fare"] == 53.1


def test_invalid_coupon_is_rejected() -> None:
    client = build_client()
    user_id = register_user(client)
    register_driver(client, "D1", "sedan")
    response = client.post(
        "/rides",
        json={
            "user_id": user_id,
            "pickup_latitude": 12.9716,
            "pickup_longitude": 77.5946,
            "requested_vehicle_type": "sedan",
            "coupon_code": "UNKNOWN",
        },
    )
    assert response.status_code == 400


def test_cannot_end_ride_twice() -> None:
    client = build_client()
    user_id = register_user(client)
    register_driver(client, "D1", "sedan")
    ride = book(client, user_id)
    assert client.post(f"/rides/{ride['id']}/end", json={"distance_km": 3}).status_code == 200
    assert client.post(f"/rides/{ride['id']}/end", json={"distance_km": 3}).status_code == 409


def test_concurrent_booking_does_not_assign_one_driver_twice() -> None:
    app = create_app(InMemoryStore())
    service = app.state.ride_service
    from app.schemas import DriverCreate, RideCreate, UserCreate, VehicleType

    users = [service.register_user(UserCreate(name=f"user-{i}")) for i in range(2)]
    driver = service.register_driver(
        DriverCreate(name="only-driver", vehicle_type=VehicleType.SEDAN, latitude=12.9716, longitude=77.5946)
    )

    def try_book(user_id: str):
        try:
            return service.book_ride(
                RideCreate(
                    user_id=user_id,
                    pickup_latitude=12.9716,
                    pickup_longitude=77.5946,
                    requested_vehicle_type=VehicleType.SEDAN,
                    radius_km=5,
                )
            )
        except Exception as exc:  # ServiceError expected for one competing request.
            return exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(try_book, [user.id for user in users]))

    successful = [result for result in results if not isinstance(result, Exception)]
    assert len(successful) == 1
    assert successful[0].driver_id == driver.id
