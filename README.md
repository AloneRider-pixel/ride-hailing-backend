# Ride-Hailing Service Backend

A FastAPI backend implementing the Plazza AI-assisted ride-hailing assignment. It uses Python 3.11+, Pydantic, pytest, and in-memory storage (which the assignment explicitly permits).

## Features

### Mandatory requirements

- Register users and drivers.
- Update a driver's GPS location.
- Book a ride by finding an available eligible driver within a radius.
- End a ride and calculate its cost.
- Progressive tiered pricing with a minimum fare and distinct Sedan/Hatchback rate cards.
- Upgrade an unavailable Hatchback request to a Sedan at no extra cost; the requested Hatchback rate is retained.
- Show ongoing and completed ride histories for users and drivers (cancelled rides are available via the same status filter too).
- Create/delete coupons and apply a valid coupon at booking. Coupon terms are snapshotted when booked.

### Bonus requirements

- **Surge pricing:** a pluggable demand/supply multiplier based on a coarse pickup-area cell. Default multiplier tiers are 1.0x when demand/supply <= 1, 1.2x when <= 1.5, 1.5x when <= 2, and 2.0x above 2. The multiplier is snapshotted at booking so the booked ride's price does not change while in progress. `NoSurgePricing` can be injected if surge must be disabled.
- **Configurable driver matching:** `nearest` and `highest_rated` strategies implement the same matching interface and can be selected at startup without changing booking logic. Set `RIDE_MATCHING_STRATEGY=nearest` or `RIDE_MATCHING_STRATEGY=highest_rated`, or pass `matching_strategy` to `create_app` for testing/integration.
- **Ride cancellation:** `POST /rides/{ride_id}/cancel` applies a cancellation policy, marks the ride cancelled, records the fee, and releases the driver. The default policy is an explicit assumption: cancellation is free for the first 120 seconds after booking; after that the fee is ₹30. This policy is configurable by injecting `CancellationFeePolicy`.
- **Concurrency safety:** a process-local reentrant lock makes driver selection/reservation atomic within one application process. It does not replace database/distributed locking across multiple workers.

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
python -m pip install -e '.[dev]'
uvicorn app.main:app --reload
```

Open `http://127.0.0.1:8000/docs` for interactive API documentation.

Run the full test suite:

```bash
python -m pytest -q
```

The GitHub Actions workflow also runs the test suite for pushes and pull requests.

## API reference

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | Health check |
| `GET` | `/system/config` | Active matching, surge and cancellation policy details |
| `POST` | `/users` | Register a user |
| `POST` | `/drivers` | Register a driver, vehicle type, GPS position and rating (0–5) |
| `GET` | `/drivers/{driver_id}` | Read driver availability/location/rating |
| `PATCH` | `/drivers/{driver_id}/location` | Update driver GPS position |
| `POST` | `/coupons` | Create a fixed or percentage coupon |
| `DELETE` | `/coupons/{code}` | Delete a coupon |
| `POST` | `/rides` | Book a ride and reserve a driver using configured strategy |
| `POST` | `/rides/{ride_id}/end` | End ride and return itemized fare |
| `POST` | `/rides/{ride_id}/cancel` | Cancel an ongoing ride and apply cancellation fee policy |
| `GET` | `/users/{user_id}/rides?status=ongoing` | User ride history; status can be `ongoing`, `completed`, or `cancelled` |
| `GET` | `/drivers/{driver_id}/rides?status=completed` | Driver ride history with optional status filter |

### Example requests

Register a user:

```bash
curl -X POST http://127.0.0.1:8000/users \
  -H 'Content-Type: application/json' \
  -d '{"name":"Himanshu"}'
```

Register a driver with a rating:

```bash
curl -X POST http://127.0.0.1:8000/drivers \
  -H 'Content-Type: application/json' \
  -d '{"name":"Driver One","vehicle_type":"sedan","latitude":12.9716,"longitude":77.5946,"rating":4.8}'
```

Book a ride (use the IDs returned by registration):

```bash
curl -X POST http://127.0.0.1:8000/rides \
  -H 'Content-Type: application/json' \
  -d '{"user_id":"USER_ID","pickup_latitude":12.9716,"pickup_longitude":77.5946,"requested_vehicle_type":"sedan","radius_km":5}'
```

End a ride using the measured route distance (not straight-line GPS distance):

```bash
curl -X POST http://127.0.0.1:8000/rides/RIDE_ID/end \
  -H 'Content-Type: application/json' \
  -d '{"distance_km":8}'
```

Cancel an ongoing ride:

```bash
curl -X POST http://127.0.0.1:8000/rides/RIDE_ID/cancel
```

## Pricing and policy assumptions

1. **Sedan pricing uses the assignment example:** minimum ₹50; first 2 km at ₹10/km; next 3 km (3–5 km) at ₹8/km; distance above 5 km at ₹5/km. Tiers are progressive. For 8 km, the metered calculation is ₹20 + ₹24 + ₹15 = ₹59.
2. **Hatchback rate card is an explicit assumption** because the assignment requires a different rate but does not specify its values: minimum ₹40; first 2 km at ₹8/km; next 3 km at ₹6/km; distance above 5 km at ₹4/km.
3. The minimum fare is applied to the normal fare before the surge multiplier. Surge is then applied, followed by any coupon discount. Thus, `base_fare` is pre-surge, `surged_fare` is the price before coupons, and `final_fare` is the payable amount.
4. **Surge area:** the default strategy groups pickup and driver coordinates into 0.05-degree latitude/longitude cells (roughly 5 km in Bengaluru, depending on longitude). Demand counts ongoing rides in that cell plus the new request; supply counts available drivers in the same cell. The strategy interface can be replaced with a service/area-provider-backed implementation.
5. **Matching:** nearest matching sorts by distance, then driver ID for deterministic ties. Highest-rated matching sorts by descending rating, then distance, then ID. Ratings are provided at driver registration for this self-contained exercise; a real service would maintain ratings from completed rides.
6. **No-cost upgrade:** if a Hatchback was requested and none is eligible inside the radius, an available Sedan within that radius may be assigned; fare remains at the Hatchback rate. Sedan requests are not downgraded.
7. **Cancellation policy:** for demonstration, cancellation is free for 120 seconds after booking and ₹30 afterward. The policy and values are injectable/configurable; exact fee terms were not supplied by the brief. Cancellation terminally marks the ride and releases its driver.
8. **Coupon lifecycle:** coupon validity is checked at booking, and its discount terms are snapshotted on the ride. Deleting a coupon later does not change a previously booked ride. Fixed or percentage discounts cannot reduce the final fare below zero.
9. **Storage and locking:** state lives in Python dictionaries and is lost on restart. An `RLock` prevents one process from assigning the same driver to two simultaneous bookings; production needs a transactional database and cross-process atomic reservation.
10. Authentication, payment processing, route-provider integration, cancellation-fee payment collection, persistent storage, and distributed locking are outside this exercise's scope.

## Architecture

- `app/main.py` — FastAPI routes, runtime configuration, error mapping.
- `app/schemas.py` — request, response, and domain schemas.
- `app/service.py` — business workflows, driver reservation, surge snapshot, cancellation and history.
- `app/matching.py` — pluggable nearest and highest-rated driver strategies.
- `app/surge.py` — pluggable demand/supply surge policy and no-surge policy.
- `app/pricing.py` — vehicle rate cards, tiered fare, and coupon discount arithmetic.
- `app/store.py` — in-memory repositories and process-local lock.
- `tests/` — unit, API, bonus feature, and concurrency tests.

## AI usage note

ChatGPT assistance was used to draft and revise the domain models, API surface, strategy interfaces, tests, and documentation. The implementation was checked against the assignment's mandatory requirements and bonus list, and the full test suite was run locally. Before submission, review the code and update this note with your actual tool names/prompts and any changes you personally make; the assignment expects you to explain and own every submitted line, including AI-generated code.

## What I would improve with more time

- Replace dictionaries with a persistent database and enforce reservations with row-level locks or atomic conditional updates.
- Add authentication/authorization, request idempotency, structured logs, metrics/tracing, and deployment configuration.
- Add real route-distance integration and an area-demand provider instead of coarse GPS cells.
- Maintain driver ratings from verified completed-trip feedback and consider cancellations in matching.
- Expand cancellation policy to account for driver arrival/travel state and ensure fees are actually charged through a payment layer.
- Add load tests, property-based fare tests, and integration tests across multiple workers.

## Production caveat

This is an assignment-sized implementation, not a production-ready ride-hailing service. In particular, in-memory storage and process-local locking do not preserve state or coordinate reservations across multiple workers. Authentication, real payment collection, route distance, operational monitoring, and abuse protection are not included.
