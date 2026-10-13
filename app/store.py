from __future__ import annotations

from threading import RLock

from app.schemas import Coupon, Driver, RideRecord, User


class InMemoryStore:
    """Small in-memory repository for the assignment.

    RLock allows service methods to perform a complete read-check-write operation
    atomically within this single process. It is not a distributed lock.
    """

    def __init__(self) -> None:
        self.users: dict[str, User] = {}
        self.drivers: dict[str, Driver] = {}
        self.rides: dict[str, RideRecord] = {}
        self.coupons: dict[str, Coupon] = {}
        self.lock = RLock()
