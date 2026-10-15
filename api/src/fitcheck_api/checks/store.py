"""Every read and write of the checks table, with the limits enforced by conditional writes.

Three kinds of item share the table (key pk):
- check#<id>: one analysis, its job description, progress and result (TTL 30 days);
- rate#<ip hash>#<day>: new analyses one client started that UTC day (TTL 2 days);
- spend#<month>: committed (settled plus reserved), reserved and settled, in
  whole micro-dollars.

Each request is built by a pure function, so the conditions can be tested
without DynamoDB. No float is ever written. TransactWriteItems uses only Put
and Update (authorized by PutItem and UpdateItem), never ConditionCheck.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from botocore.exceptions import ClientError

from .ids import check_key, month_key, rate_key, spend_key

ACTIVE = ("queued", "extracting", "retrieving", "judging")
FINAL = ("done", "failed")
CHECK_TTL_SECONDS = 30 * 24 * 3600
RATE_TTL_SECONDS = 2 * 24 * 3600
# A check that is not final and has not moved for this long counts as failed.
STALE_SECONDS = 15 * 60
LEASE_SECONDS = 300


@dataclass(frozen=True)
class Limits:
    daily_per_ip: int
    monthly_cap_micro: int
    per_check_cap_micro: int

    @property
    def spend_room_micro(self) -> int:
        """committed may be at most this before a reservation: the condition cannot add."""
        return self.monthly_cap_micro - self.per_check_cap_micro


@dataclass(frozen=True)
class CreateOutcome:
    # created, cached, daily_limit, monthly_budget or busy
    result: str
    status: str | None = None


def _serializer():
    from boto3.dynamodb.types import TypeDeserializer, TypeSerializer

    return TypeSerializer(), TypeDeserializer()


def to_attribute_values(values: Mapping[str, Any]) -> dict[str, Any]:
    serializer, _ = _serializer()
    return {key: serializer.serialize(value) for key, value in values.items()}


def from_attribute_values(item: Mapping[str, Any]) -> dict[str, Any]:
    _, deserializer = _serializer()
    return {key: _plain(deserializer.deserialize(value)) for key, value in item.items()}


def _plain(value: Any) -> Any:
    """DynamoDB numbers come back as Decimal; the table holds only integers."""
    if isinstance(value, Decimal):
        return int(value)
    if isinstance(value, list):
        return [_plain(item) for item in value]
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    return value


def is_stale(item: Mapping[str, Any], now: float) -> bool:
    return item.get("status") in ACTIVE and int(item.get("updated_epoch", 0)) < now - STALE_SECONDS


def new_check_item(identifier: str, job_description: str, versions: Mapping[str, str], now: float, iso_now: str) -> dict[str, Any]:
    return {
        "pk": check_key(identifier),
        "check_id": identifier,
        "status": "queued",
        "job_description": job_description,
        "analysis_version": versions["analysis_version"],
        "model": versions["model"],
        "index_version": versions["index_version"],
        "created_at": iso_now,
        "created_epoch": int(now),
        "updated_at": iso_now,
        "updated_epoch": int(now),
        "spend_month": month_key(now),
        "reserved_micro": 0,
        "cost_micro": 0,
        "expires_at": int(now) + CHECK_TTL_SECONDS,
    }


def create_transaction(table: str, item: Mapping[str, Any], client_hash: str, day: str, limits: Limits, now: float) -> list[dict[str, Any]]:
    """Put the check, count it against the client's day and reserve the per-check cap, all or nothing."""
    item = {**item, "reserved_micro": limits.per_check_cap_micro}
    return [
        {
            "Put": {
                "TableName": table,
                "Item": to_attribute_values(item),
                "ConditionExpression": (
                    "attribute_not_exists(pk) OR #status = :failed "
                    "OR (#status IN (:queued, :extracting, :retrieving, :judging) AND updated_epoch < :stale)"
                ),
                "ExpressionAttributeNames": {"#status": "status"},
                "ExpressionAttributeValues": to_attribute_values(
                    {
                        ":failed": "failed",
                        ":queued": "queued",
                        ":extracting": "extracting",
                        ":retrieving": "retrieving",
                        ":judging": "judging",
                        ":stale": int(now) - STALE_SECONDS,
                    }
                ),
                "ReturnValuesOnConditionCheckFailure": "ALL_OLD",
            }
        },
        {
            "Update": {
                "TableName": table,
                "Key": to_attribute_values({"pk": rate_key(client_hash, day)}),
                "UpdateExpression": "ADD #count :one SET expires_at = :expires",
                "ConditionExpression": "attribute_not_exists(#count) OR #count < :limit",
                "ExpressionAttributeNames": {"#count": "count"},
                "ExpressionAttributeValues": to_attribute_values(
                    {":one": 1, ":limit": limits.daily_per_ip, ":expires": int(now) + RATE_TTL_SECONDS}
                ),
                "ReturnValuesOnConditionCheckFailure": "ALL_OLD",
            }
        },
        {
            "Update": {
                "TableName": table,
                "Key": to_attribute_values({"pk": spend_key(item["spend_month"])}),
                "UpdateExpression": "ADD #committed :cap, #reserved :cap",
                "ConditionExpression": "attribute_not_exists(#committed) OR #committed <= :room",
                "ExpressionAttributeNames": {"#committed": "committed", "#reserved": "reserved"},
                "ExpressionAttributeValues": to_attribute_values({":cap": limits.per_check_cap_micro, ":room": limits.spend_room_micro}),
                "ReturnValuesOnConditionCheckFailure": "ALL_OLD",
            }
        },
    ]


def read_cancellation(error: ClientError, limits: Limits) -> CreateOutcome:
    """Why the create transaction was cancelled, from the old items DynamoDB returns."""
    reasons = error.response.get("CancellationReasons") or []
    codes = [reason.get("Code") for reason in reasons]
    if len(codes) >= 1 and codes[0] == "ConditionalCheckFailed":
        old = from_attribute_values(reasons[0].get("Item") or {})
        return CreateOutcome("cached", old.get("status"))
    if len(codes) >= 2 and codes[1] == "ConditionalCheckFailed":
        return CreateOutcome("daily_limit")
    if len(codes) >= 3 and codes[2] == "ConditionalCheckFailed":
        old = from_attribute_values(reasons[2].get("Item") or {})
        settled = int(old.get("settled", 0))
        return CreateOutcome("monthly_budget" if settled > limits.spend_room_micro else "busy")
    if "TransactionConflict" in codes:
        return CreateOutcome("busy")
    raise error


def lease_request(table: str, identifier: str, now: float) -> dict[str, Any]:
    return {
        "TableName": table,
        "Key": to_attribute_values({"pk": check_key(identifier)}),
        "UpdateExpression": "SET lease_until = :until",
        "ConditionExpression": "#status IN (:queued, :extracting, :retrieving, :judging) AND (attribute_not_exists(lease_until) OR lease_until < :now)",
        "ExpressionAttributeNames": {"#status": "status"},
        "ExpressionAttributeValues": to_attribute_values(
            {
                ":until": int(now) + LEASE_SECONDS,
                ":now": int(now),
                ":queued": "queued",
                ":extracting": "extracting",
                ":retrieving": "retrieving",
                ":judging": "judging",
            }
        ),
        "ReturnValues": "ALL_NEW",
    }


def phase_update_request(table: str, identifier: str, expected: str, status: str, fields: Mapping[str, Any], now: float, iso_now: str) -> dict[str, Any]:
    """Moves a check from one status to the next, writing what the phase produced."""
    values = {**fields, "updated_at": iso_now, "updated_epoch": int(now)}
    names = {"#status": "status", **{f"#f{index}": name for index, name in enumerate(values)}}
    sets = ["#status = :next"] + [f"#f{index} = :v{index}" for index in range(len(values))]
    attribute_values = {":next": status, ":expected": expected, **{f":v{index}": value for index, value in enumerate(values.values())}}
    return {
        "TableName": table,
        "Key": to_attribute_values({"pk": check_key(identifier)}),
        "UpdateExpression": "SET " + ", ".join(sets),
        "ConditionExpression": "#status = :expected",
        "ExpressionAttributeNames": names,
        "ExpressionAttributeValues": to_attribute_values(attribute_values),
        "ReturnValues": "ALL_NEW",
    }


def fail_request(table: str, identifier: str, code: str, cost_micro: int | None, now: float, iso_now: str) -> dict[str, Any]:
    values: dict[str, Any] = {":failed": "failed", ":code": code, ":at": iso_now, ":epoch": int(now)}
    sets = "#status = :failed, #error = :code, updated_at = :at, updated_epoch = :epoch"
    if cost_micro is not None:
        values[":cost"] = cost_micro
        sets += ", cost_micro = :cost"
    values.update({":queued": "queued", ":extracting": "extracting", ":retrieving": "retrieving", ":judging": "judging"})
    return {
        "TableName": table,
        "Key": to_attribute_values({"pk": check_key(identifier)}),
        "UpdateExpression": "SET " + sets,
        "ConditionExpression": "#status IN (:queued, :extracting, :retrieving, :judging)",
        "ExpressionAttributeNames": {"#status": "status", "#error": "error"},
        "ExpressionAttributeValues": to_attribute_values(values),
    }


def settle_transaction(table: str, identifier: str, month: str, reserved_micro: int, actual_micro: int, iso_now: str) -> list[dict[str, Any]]:
    """Once per check (settled_at): the reservation goes, the real cost stays."""
    return [
        {
            "Update": {
                "TableName": table,
                "Key": to_attribute_values({"pk": check_key(identifier)}),
                "UpdateExpression": "SET settled_at = :at",
                "ConditionExpression": "attribute_exists(pk) AND attribute_not_exists(settled_at)",
                "ExpressionAttributeValues": to_attribute_values({":at": iso_now}),
            }
        },
        {
            "Update": {
                "TableName": table,
                "Key": to_attribute_values({"pk": spend_key(month)}),
                "UpdateExpression": "ADD #reserved :release, #settled :actual, #committed :difference",
                "ExpressionAttributeNames": {"#reserved": "reserved", "#settled": "settled", "#committed": "committed"},
                "ExpressionAttributeValues": to_attribute_values(
                    {":release": -reserved_micro, ":actual": actual_micro, ":difference": actual_micro - reserved_micro}
                ),
            }
        },
    ]


def _is_conditional_failure(error: ClientError) -> bool:
    return error.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException"


class ChecksStore:
    """The table, through a boto3 DynamoDB client."""

    def __init__(self, client: Any, table: str) -> None:
        self.client = client
        self.table = table

    def get(self, identifier: str) -> dict[str, Any] | None:
        response = self.client.get_item(TableName=self.table, Key=to_attribute_values({"pk": check_key(identifier)}), ConsistentRead=True)
        item = response.get("Item")
        return from_attribute_values(item) if item else None

    def create(self, item: Mapping[str, Any], client_hash: str, day: str, limits: Limits, now: float) -> CreateOutcome:
        try:
            self.client.transact_write_items(TransactItems=create_transaction(self.table, item, client_hash, day, limits, now))
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") != "TransactionCanceledException":
                raise
            return read_cancellation(error, limits)
        return CreateOutcome("created", "queued")

    def release_reservation(self, month: str, reserved_micro: int) -> None:
        """For a check that was never queued: drop its reservation, nothing was spent."""
        self.client.update_item(
            TableName=self.table,
            Key=to_attribute_values({"pk": spend_key(month)}),
            UpdateExpression="ADD #reserved :release, #committed :release",
            ExpressionAttributeNames={"#reserved": "reserved", "#committed": "committed"},
            ExpressionAttributeValues=to_attribute_values({":release": -reserved_micro}),
        )

    def lease(self, identifier: str, now: float) -> dict[str, Any] | None:
        """The check, if this worker may run it; None for a duplicate delivery or a finished check."""
        try:
            response = self.client.update_item(**lease_request(self.table, identifier, now))
        except ClientError as error:
            if _is_conditional_failure(error):
                return None
            raise
        return from_attribute_values(response["Attributes"])

    def advance(self, identifier: str, expected: str, status: str, fields: Mapping[str, Any], now: float, iso_now: str) -> dict[str, Any]:
        response = self.client.update_item(**phase_update_request(self.table, identifier, expected, status, fields, now, iso_now))
        return from_attribute_values(response["Attributes"])

    def fail(self, identifier: str, code: str, cost_micro: int | None, now: float, iso_now: str) -> bool:
        try:
            self.client.update_item(**fail_request(self.table, identifier, code, cost_micro, now, iso_now))
        except ClientError as error:
            if _is_conditional_failure(error):
                return False
            raise
        return True

    def settle(self, identifier: str, month: str, reserved_micro: int, actual_micro: int, iso_now: str) -> bool:
        try:
            self.client.transact_write_items(TransactItems=settle_transaction(self.table, identifier, month, reserved_micro, actual_micro, iso_now))
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") == "TransactionCanceledException":
                return False
            raise
        return True
