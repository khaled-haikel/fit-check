"""An in-memory stand-in for ChecksStore with the same outcomes as the conditional writes."""

import copy

from fitcheck_api.checks.ids import check_key, rate_key, spend_key
from fitcheck_api.checks.store import ACTIVE, CreateOutcome, is_stale


class FakeStore:
    def __init__(self):
        self.items: dict[str, dict] = {}
        self.calls: list[str] = []

    def spend(self, month: str) -> dict:
        return self.items.setdefault(spend_key(month), {"committed": 0, "reserved": 0, "settled": 0})

    def get(self, identifier):
        self.calls.append("get")
        item = self.items.get(check_key(identifier))
        return copy.deepcopy(item) if item else None

    def create(self, item, client_hash, day, limits, now):
        self.calls.append("create")
        old = self.items.get(item["pk"])
        if old and old["status"] != "failed" and not is_stale(old, now):
            return CreateOutcome("cached", old["status"])
        rate = self.items.setdefault(rate_key(client_hash, day), {"count": 0})
        if rate["count"] >= limits.daily_per_ip:
            return CreateOutcome("daily_limit")
        spend = self.spend(item["spend_month"])
        if spend["committed"] > limits.spend_room_micro:
            return CreateOutcome("monthly_budget" if spend["settled"] > limits.spend_room_micro else "busy")
        self.items[item["pk"]] = {**copy.deepcopy(item), "reserved_micro": limits.per_check_cap_micro}
        rate["count"] += 1
        spend["committed"] += limits.per_check_cap_micro
        spend["reserved"] += limits.per_check_cap_micro
        return CreateOutcome("created", "queued")

    def release_reservation(self, month, reserved_micro):
        self.calls.append("release_reservation")
        spend = self.spend(month)
        spend["reserved"] -= reserved_micro
        spend["committed"] -= reserved_micro

    def lease(self, identifier, now):
        self.calls.append("lease")
        item = self.items.get(check_key(identifier))
        if not item or item["status"] not in ACTIVE or item.get("lease_until", 0) >= now:
            return None
        item["lease_until"] = int(now) + 300
        return copy.deepcopy(item)

    def advance(self, identifier, expected, status, fields, now, iso_now):
        self.calls.append(f"advance:{expected}->{status}")
        item = self.items[check_key(identifier)]
        assert item["status"] == expected, (item["status"], expected)
        item.update(copy.deepcopy(dict(fields)), status=status, updated_at=iso_now, updated_epoch=int(now))
        return copy.deepcopy(item)

    def fail(self, identifier, code, cost_micro, now, iso_now):
        self.calls.append(f"fail:{code}")
        item = self.items.get(check_key(identifier))
        if not item or item["status"] not in ACTIVE:
            return False
        item.update(status="failed", error=code, updated_at=iso_now, updated_epoch=int(now))
        if cost_micro is not None:
            item["cost_micro"] = cost_micro
        return True

    def settle(self, identifier, month, reserved_micro, actual_micro, iso_now):
        self.calls.append("settle")
        item = self.items[check_key(identifier)]
        if "settled_at" in item:
            return False
        item["settled_at"] = iso_now
        spend = self.spend(month)
        spend["reserved"] -= reserved_micro
        spend["settled"] += actual_micro
        spend["committed"] += actual_micro - reserved_micro
        return True
