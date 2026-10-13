# Plazza Ride-Hailing Backend — 30-Minute Demo Runbook

This follows the assignment's demo format: 8 minutes showing the solution, 8 minutes explaining code/design, 10 minutes for a live extension, and 4 minutes for Q&A.

## Before the call

From the repository root:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
python -m pytest -q
python -m uvicorn app.main:app --reload
```

Open `http://127.0.0.1:8000/docs` and keep your editor/terminal ready. Restarting Uvicorn clears in-memory data.

## Open with assumptions (30–45 seconds)

Say: “The assignment permits in-memory storage, so this uses dictionaries protected by a process-local reentrant lock. Sedan pricing follows the PDF exactly. The PDF doesn't provide Hatchback rates or cancellation amounts, so I documented a sample Hatchback rate card and a demo policy: free cancellation for 120 seconds, then ₹30. Ride distance is supplied as a measured route distance; a routing provider wasn't required.”

## Part 1 — Working solution in action (8 minutes)

Use Swagger UI at `/docs`. Save IDs from each response and replace placeholders.

### 1. Show configuration
Run `GET /system/config`. Point out the configured fare, matching, surge and cancellation policies.

### 2. Register one user and three nearby Sedan drivers
`POST /users`:
```json
{"name":"Demo User A"}
```
Repeat for users B and C for the surge example.

`POST /drivers` (same local area; vary name, location, and rating):
```json
{"name":"Driver Near","vehicle_type":"sedan","latitude":12.9716,"longitude":77.5946,"rating":4.2}
```
```json
{"name":"Driver Mid","vehicle_type":"sedan","latitude":12.9720,"longitude":77.5946,"rating":4.8}
```
```json
{"name":"Driver Far","vehicle_type":"sedan","latitude":12.9740,"longitude":77.5946,"rating":4.5}
```
Use pickup coordinates `12.9716, 77.5946`. These are illustrative locations, not production data.

### 3. Book and complete a normal Sedan ride
`POST /rides`:
```json
{"user_id":"USER_A_ID","pickup_latitude":12.9716,"pickup_longitude":77.5946,"requested_vehicle_type":"sedan","radius_km":5}
```
Show that a nearby driver is selected and becomes unavailable.

Then `POST /rides/RIDE_ID/end`:
```json
{"distance_km":8}
```
Sedan base fare for 8 km is `2×₹10 + 3×₹8 + 3×₹5 = ₹59`. Point out the base fare, surge, discount, and final-fare fields.

### 4. Show coupon application
`POST /coupons`:
```json
{"code":"SAVE10","discount_type":"percentage","value":10,"max_discount":20}
```
Book another ride using `"coupon_code":"SAVE10"`, then end at 8 km. With no surge, the discount is ₹5.90 and final fare ₹53.10. Explain that coupon terms are snapshotted at booking.

### 5. Show free Hatchback-to-Sedan upgrade
Do not register a Hatchback driver in the pickup area. Book a ride with `"requested_vehicle_type":"hatchback"`. The response should show a Sedan assigned, `upgraded_at_no_extra_cost: true`, and `pricing_vehicle_type: "hatchback"`. At 8 km, the documented sample Hatchback fare is ₹46 before surge/coupons. Explain this price is an assumption because the PDF gives no Hatchback rate.

### 6. Show surge pricing
With three available Sedan drivers and three users in the same area, create three ongoing bookings in sequence without completing them first. Default expected multipliers: ride 1 = 1.0x, ride 2 = 1.0x, ride 3 = 2.0x as local supply drops. Complete the rides afterward to release drivers.

### 7. Show edge cases and histories
- Try a booking at pickup coordinates `28.6139, 77.2090` (Delhi) with a radius of 1 km; expect `409` when there is no eligible driver.
- Show `GET /users/USER_A_ID/rides?status=completed` and `GET /drivers/DRIVER_ID/rides?status=completed`.
- Optionally show `PATCH /drivers/DRIVER_ID/location` followed by `GET /drivers/DRIVER_ID`.

If short on time, prioritise one complete ride, coupon, free upgrade, and one failure case.

## Part 2 — Code/design walkthrough (8 minutes)

Open these files and describe one clear responsibility for each:

1. `app/main.py` — API routes, policy injection, request/response handling and error mapping.
2. `app/schemas.py` — validated request/response shapes and enums.
3. `app/service.py` — booking flow, radius eligibility, free-upgrade fallback, atomic driver reservation, ride state, and coupon/surge snapshots.
4. `app/matching.py` — `DriverMatchingStrategy`, `nearest`, and `highest_rated`; matching is not hardcoded into booking.
5. `app/pricing.py` — `FarePricingStrategy`, `ProgressiveTierPricing`, vehicle rate cards, and 8 km Sedan calculation.
6. `app/surge.py` — demand/supply surge and the `NoSurgePricing` alternative.
7. `app/store.py` — in-memory dictionaries and `RLock`; clarify that this is not a distributed lock.
8. `tests/test_pricing.py`, `tests/test_api.py`, `tests/test_bonus_features.py` — assertions, error cases and concurrency test.

Run `python -m pytest -q` during the walkthrough. State the result your terminal actually prints.

## Part 3 — Live extension (10 minutes)

Suggested exercise: **add an SUV vehicle type** as a small end-to-end extension. Do this live; don't claim it was part of the original implementation.

### Scope out loud (first minute)

“I'll add the vehicle type through the existing enum and pricing rate-card config, then add a pricing unit test and an API-level test. I expect booking logic not to need new conditionals because it already uses the vehicle enum and pricing strategy.”

### Implement (about 4 minutes)

1. In `app/schemas.py`, add `SUV = "suv"` to `VehicleType`.
2. In `app/pricing.py`, add a `VehiclePricing` entry for `VehicleType.SUV`; e.g., minimum ₹60, first 2 km at ₹12/km, next 3 km at ₹10/km, over 5 km at ₹7/km. State clearly that these are demo assumptions because the PDF doesn't define SUV rates.
3. Add a unit test in `tests/test_pricing.py` for the SUV minimum fare and an 8 km progressive fare.
4. Add an API test: register an SUV driver and a user, book an SUV, complete it, and assert the assigned vehicle and fare.

### Verify (about 5 minutes)

```bash
python -m pytest -q tests/test_pricing.py -k suv
python -m pytest -q tests/test_bonus_features.py -k suv
python -m pytest -q
```

If you put the API test in another file, point the command at that file. If a test fails, inspect the traceback, calculate the tier arithmetic, change the code/test, and rerun. Never claim success until the command confirms it. If time is short, complete the enum + rate card + pricing test and explain what remains.

## Part 4 — Q&A (4 minutes)

**Why in-memory?** The PDF explicitly permits it and values full domain modelling; production needs persistent storage.

**Does the lock protect multiple workers?** No. It protects one process only. Production needs transaction/row-level locking or an atomic conditional reservation in a shared database.

**Why snapshot surge and coupon terms?** A booked ride's price should not change when area demand or coupon configuration changes later.

**Why charge a Sedan upgrade at Hatchback rates?** The requirement says no extra cost, so pricing follows the requested type.

**How did you verify correctness?** Describe tests you actually ran and show their output, including edge/failure cases.

**How did you use AI?** Be specific about the prompts, output you changed/rejected, and what tests showed. Do not invent prompts or reviews.

**What would improve for production?** Persistent data, distributed reservation safety, auth, idempotency, route integration, payment collection for cancellation fees, observability and load testing.

## Final checklist

- [ ] Fresh install works; full test suite passes immediately before the demo.
- [ ] Uvicorn starts and `/docs` loads.
- [ ] State assumptions before clicking through the API.
- [ ] No secrets, credentials, or personal data appear on screen.
- [ ] Keep returned IDs visible and explain what each endpoint proves.
- [ ] Make the live extension in front of the evaluator, explain scope, and test it.
- [ ] Keep the live extension in a separate branch or commit afterward only if appropriate.

## Important format note

The assignment describes a 30-minute demo session (8 + 8 + 10 + 4 minutes); it does not explicitly require uploading a prerecorded video. Follow any separate instructions from the recruiter if they specifically request a recording.
