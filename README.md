# Ride-Hailing Service Backend

A small FastAPI backend for the ride-hailing assignment. It uses Python 3.11+ and in-memory storage, which is explicitly allowed by the brief. The implementation prioritizes complete mandatory functionality, understandable code, and automated tests over infrastructure and optional bonus features.

## Features

- Register users and drivers.
- Update a driver's GPS location.
- Book the nearest available eligible driver within a caller-specified radius.
- Atomically reserve a driver during booking within this process.
- Complete a ride using the measured trip distance and return an itemized fare.
- Progressive per-kilometer pricing with a minimum fare and separate Sedan/Hatchback rate cards.
- No-extra-cost Hatchback-to-Sedan upgrade when no available Hatchback is inside the pickup radius.
- User and driver ride histories with optional `ongoing`/`completed` filtering.
- Create and delete fixed or percentage coupons; validate coupons at booking.
- Snapshot coupon terms on booking so deleting the coupon does not alter an already-booked ride.
- Tests covering pricing tiers, minimum fare, coupons, the vehicle upgrade, ride lifecycle, matching failure, and concurrent booking.

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\\Scripts\\activate
python -m pip install -e '.[dev]'
uvicorn app.main:app --reload
```

Open `http://127.0.0.1:8000/docs` for the interactive API documentation.

Run tests:

```bash
pytest
```

## API overview

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | Health check |
| `POST` | `/users` | Register a user |
| `POST` | `/drivers` | Register a driver with vehicle type and location |
| `GET` | `/drivers/{driver_id}` | Inspect driver availability/location |
| `PATCH` | `/drivers/{driver_id}/location` | Update driver location |
| `POST` | `/coupons` | Create a coupon |
| `DELETE` | `/coupons/{code}` | Delete a coupon |
| `POST` | `/rides` | Book a ride and reserve a driver |
| `POST` | `/rides/{ride_id}/end` | End a ride and calculate the fare |
| `GET` | `/users/{user_id}/rides` | User ride history; optional `?status=ongoing` or `?status=completed` |
| `GET` | `/drivers/{driver_id}/rides` | Driver ride history; same optional status filter |

### Example workflow

1. Register a user:

```bash
curl -X POST http://127.0.0.1:8000/users \
  -H 'Content-Type: application/json' \
  -d '{"name":"Himanshu"}'
```

2. Register a nearby Sedan driver (replace IDs in later calls with returned IDs):

```bash
curl -X POST http://127.0.0.1:8000/drivers \
  -H 'Content-Type: application/json' \
  -d '{"name":"Driver One","vehicle_type":"sedan","latitude":12.9716,"longitude":77.5946}'
```

3. Create a 10% coupon with an optional ₹20 discount cap:

```bash
curl -X POST http://127.0.0.1:8000/coupons \
  -H 'Content-Type: application/json' \
  -d '{"code":"SAVE10","discount_type":"percentage","value":10,"max_discount":20}'
```

4. Book a ride (include the actual user ID returned in step 1):

```bash
curl -X POST http://127.0.0.1:8000/rides \
  -H 'Content-Type: application/json' \
  -d '{"user_id":"USER_ID","pickup_latitude":12.9716,"pickup_longitude":77.5946,"requested_vehicle_type":"sedan","radius_km":5,"coupon_code":"SAVE10"}'
```

5. Complete the ride using a measured/route distance in kilometers (replace `RIDE_ID`):

```bash
curl -X POST http://127.0.0.1:8000/rides/RIDE_ID/end \
  -H 'Content-Type: application/json' \
  -d '{"distance_km":8}'
```

The fare response includes `base_fare`, `discount_amount`, and `final_fare` in INR.

## Assumptions and design decisions

1. **In-memory state:** Registration, rides, drivers, and coupons reset when the process restarts. This is suitable for the assignment; production would require a persistent database.
2. **Coordinates and matching:** Driver-to-pickup distance uses the Haversine formula over GPS coordinates. The nearest available eligible driver is selected. The radius is supplied per booking and defaults to 5 km.
3. **Trip distance:** The end-ride request supplies `distance_km`, representing a measured route distance. Straight-line pickup distance is not treated as the driven distance.
4. **Pricing tiers:** The Sedan card uses the assignment's example: minimum ₹50; first 2 km at ₹10/km; next 3 km at ₹8/km; distance above 5 km at ₹5/km. Hatchback uses a distinct cheaper card: minimum ₹40; first 2 km at ₹8/km; next 3 km at ₹6/km; distance above 5 km at ₹4/km. Tiers are progressive, not a single rate applied to the entire trip.
5. **Minimum fare and coupons:** Minimum fare is applied to the pre-discount base fare. Percentage coupons may have a cap; fixed or percentage discounts cannot reduce the final fare below ₹0. A discount can bring the final payable fare below the base minimum.
6. **Coupon lifecycle:** A coupon is validated when the ride is booked. Its terms are snapshotted on the ride, so deleting the coupon afterward does not change that booked ride's fare. Coupon usage limits are not implemented because none were required.
7. **Vehicle upgrade:** If the user requests a Hatchback and no available Hatchback is inside the pickup radius, the service may assign the nearest available Sedan in radius. The ride is priced using the Hatchback rate card so the upgrade has no additional cost. A Sedan request does not automatically downgrade to a Hatchback.
8. **Driver availability:** A driver is marked unavailable when assigned and becomes available again when the ride ends. Driver availability is guarded with a process-local `RLock` during selection and state changes.
9. **Error handling:** Unknown users, drivers, rides, or coupons return suitable 4xx errors. Trying to book without an eligible driver or ending a ride twice returns `409 Conflict`.
10. **Out of scope:** Authentication/authorization, routing-provider integration, geospatial database indexes, persistence, payment processing, cancellation fees, surge pricing, and multi-process/distributed locking are not implemented.

## Architecture

- `app/main.py` — FastAPI routes, request/response handling, and error mapping.
- `app/schemas.py` — validated request/response/domain schemas.
- `app/service.py` — business workflows, matching, ride state transitions, coupon snapshots, and history.
- `app/pricing.py` — vehicle rate cards and discount calculation.
- `app/store.py` — in-memory repositories and a process-local lock.
- `tests/` — unit and API-level tests.

## AI usage note

AI assistance was used to help scaffold the API/domain structure, propose test cases, and draft the documentation. The implementation choices were then aligned to the supplied requirements: in-memory storage was retained to keep scope within two hours; pricing/matching/coupon logic was separated from HTTP handlers; the bonus scope was deferred until mandatory behaviour was covered; and a process-local lock plus a concurrent-booking test were added to address the specified driver-booking race. The candidate submitting this assignment should personally run the tests, inspect every file, adjust the implementation as needed, and be able to explain and modify every submitted line. Update this note to reflect the exact tools, prompts, and review changes made before final submission.

## What I would improve with more time

- Add a database-backed repository with migrations and transaction/locking semantics so the service survives restarts and scales across workers.
- Add authentication and authorization, API idempotency keys, structured logs, metrics, and trace correlation.
- Add more tests for malformed coordinates, invalid coupon definitions, boundary distances, and booking races under higher contention.
- Integrate a route-distance provider rather than accepting the measured trip distance from the caller.
- Add configurable driver-matching strategies, surge pricing, cancellation policy, and a new vehicle type through configuration.

## Before a production deployment

Replace in-memory dictionaries with a transactional database; enforce driver reservation with row-level locks or an atomic conditional update; add authentication, authorization, idempotency keys, structured observability, migrations, load tests, and deployment configuration. The process-local lock only protects requests inside one running Python process.
