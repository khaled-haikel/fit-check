"""IDs, keys and the DynamoDB requests behind the limits, without DynamoDB."""

import pytest
from botocore.exceptions import ClientError

from fitcheck_api.checks.ids import CHECK_ID, check_id, day_key, ip_hash, month_key
from fitcheck_api.checks.store import (
    STALE_SECONDS,
    ChecksStore,
    Limits,
    create_transaction,
    fail_request,
    from_attribute_values,
    lease_request,
    new_check_item,
    phase_update_request,
    read_cancellation,
    settle_transaction,
    to_attribute_values,
)

NOW = 1_791_892_800  # 2026-10-13T12:00:00Z
LIMITS = Limits(daily_per_ip=5, monthly_cap_micro=2_000_000, per_check_cap_micro=500_000)
VERSIONS = {"analysis_version": "a" * 64, "model": "us.anthropic.claude-sonnet-4-6", "index_version": "i" * 64}


def test_the_check_id_is_deterministic_base32():
    first = check_id("version-1", "A job description.")

    assert first == check_id("version-1", "A job description.")
    assert first != check_id("version-2", "A job description.")
    assert first != check_id("version-1", "Another job description.")
    assert CHECK_ID.match(first) and len(first) == 26


def test_the_ip_is_hashed_and_dates_are_utc():
    assert ip_hash("203.0.113.10") == ip_hash("203.0.113.10") != ip_hash("203.0.113.11")
    assert len(ip_hash("203.0.113.10")) == 16 and "203" not in ip_hash("203.0.113.10")
    assert day_key(NOW) == "2026-10-13" and month_key(NOW) == "2026-10"


def test_the_spend_room_is_computed_in_code():
    assert LIMITS.spend_room_micro == 1_500_000


def test_no_float_reaches_dynamodb():
    item = new_check_item("x" * 26, "job", VERSIONS, NOW, "2026-10-13T12:00:00Z")
    with pytest.raises(TypeError):
        to_attribute_values({"cost": 0.12})
    assert from_attribute_values(to_attribute_values(item)) == item


def test_the_create_transaction_puts_counts_and_reserves():
    item = new_check_item("x" * 26, "job", VERSIONS, NOW, "2026-10-13T12:00:00Z")

    put, rate, spend = create_transaction("t", item, "ffffffffffffffff", "2026-10-13", LIMITS, NOW)

    assert "ConditionCheck" not in put and "ConditionCheck" not in rate and "ConditionCheck" not in spend
    assert put["Put"]["ConditionExpression"].startswith("attribute_not_exists(pk) OR #status = :failed")
    assert put["Put"]["ExpressionAttributeValues"][":stale"] == {"N": str(NOW - STALE_SECONDS)}
    assert put["Put"]["Item"]["reserved_micro"] == {"N": "500000"}
    assert rate["Update"]["Key"] == {"pk": {"S": "rate#ffffffffffffffff#2026-10-13"}}
    assert rate["Update"]["ConditionExpression"] == "attribute_not_exists(#count) OR #count < :limit"
    assert rate["Update"]["ExpressionAttributeValues"][":limit"] == {"N": "5"}
    assert spend["Update"]["Key"] == {"pk": {"S": "spend#2026-10"}}
    assert spend["Update"]["UpdateExpression"] == "ADD #committed :cap, #reserved :cap"
    assert spend["Update"]["ConditionExpression"] == "attribute_not_exists(#committed) OR #committed <= :room"
    assert spend["Update"]["ExpressionAttributeValues"][":room"] == {"N": "1500000"}
    for operation in (put["Put"], rate["Update"], spend["Update"]):
        assert operation["ReturnValuesOnConditionCheckFailure"] == "ALL_OLD"
        assert "+" not in operation["ConditionExpression"] and " - " not in operation["ConditionExpression"]


def _cancelled(*reasons):
    return ClientError({"Error": {"Code": "TransactionCanceledException", "Message": "cancelled"}, "CancellationReasons": list(reasons)}, "TransactWriteItems")


NONE = {"Code": "None"}


@pytest.mark.parametrize(
    ("reasons", "result", "status"),
    [
        (({"Code": "ConditionalCheckFailed", "Item": to_attribute_values({"status": "done"})}, NONE, NONE), "cached", "done"),
        ((NONE, {"Code": "ConditionalCheckFailed", "Item": to_attribute_values({"count": 5})}, NONE), "daily_limit", None),
        ((NONE, NONE, {"Code": "ConditionalCheckFailed", "Item": to_attribute_values({"committed": 1_900_000, "settled": 1_600_000})}), "monthly_budget", None),
        ((NONE, NONE, {"Code": "ConditionalCheckFailed", "Item": to_attribute_values({"committed": 1_600_000, "settled": 600_000})}), "busy", None),
        ((NONE, {"Code": "TransactionConflict"}, NONE), "busy", None),
    ],
    ids=["cached", "daily-limit", "monthly-budget", "busy-reservations", "conflict"],
)
def test_cancellation_reasons(reasons, result, status):
    outcome = read_cancellation(_cancelled(*reasons), LIMITS)

    assert (outcome.result, outcome.status) == (result, status)


def test_other_cancellations_are_not_swallowed():
    with pytest.raises(ClientError):
        read_cancellation(_cancelled(NONE, NONE, {"Code": "ValidationError"}), LIMITS)


def test_the_lease_waits_for_an_active_unleased_check():
    request = lease_request("t", "x" * 26, NOW)

    assert request["ConditionExpression"] == (
        "#status IN (:queued, :extracting, :retrieving, :judging) AND (attribute_not_exists(lease_until) OR lease_until < :now)"
    )
    assert request["ExpressionAttributeValues"][":until"] == {"N": str(NOW + 300)}


def test_phase_updates_and_failures_are_conditional():
    advance = phase_update_request("t", "x" * 26, "extracting", "retrieving", {"requirements": [{"id": "r01"}]}, NOW, "now")
    fail = fail_request("t", "x" * 26, "deadline", 1234, NOW, "now")

    assert advance["ConditionExpression"] == "#status = :expected"
    assert advance["ExpressionAttributeValues"][":expected"] == {"S": "extracting"}
    assert fail["ConditionExpression"] == "#status IN (:queued, :extracting, :retrieving, :judging)"
    assert fail["ExpressionAttributeValues"][":cost"] == {"N": "1234"}


def test_settlement_happens_once_and_moves_committed_by_the_difference():
    check, spend = settle_transaction("t", "x" * 26, "2026-10", 500_000, 120_000, "now")

    assert check["Update"]["ConditionExpression"] == "attribute_exists(pk) AND attribute_not_exists(settled_at)"
    values = spend["Update"]["ExpressionAttributeValues"]
    assert values[":release"] == {"N": "-500000"} and values[":actual"] == {"N": "120000"} and values[":difference"] == {"N": "-380000"}


class FakeClient:
    def __init__(self, error=None, attributes=None):
        self.error, self.attributes, self.calls = error, attributes, []

    def transact_write_items(self, **kwargs):
        self.calls.append(("transact", kwargs))
        if self.error:
            raise self.error

    def update_item(self, **kwargs):
        self.calls.append(("update", kwargs))
        if self.error:
            raise self.error
        return {"Attributes": to_attribute_values(self.attributes or {})}


def test_the_store_maps_a_cancelled_create():
    item = new_check_item("x" * 26, "job", VERSIONS, NOW, "now")
    store = ChecksStore(FakeClient(_cancelled(NONE, {"Code": "ConditionalCheckFailed"}, NONE)), "t")

    assert store.create(item, "h", "2026-10-13", LIMITS, NOW).result == "daily_limit"
    assert ChecksStore(FakeClient(), "t").create(item, "h", "2026-10-13", LIMITS, NOW).result == "created"


def test_a_duplicate_delivery_gets_no_lease_and_a_second_settlement_is_refused():
    failed = ClientError({"Error": {"Code": "ConditionalCheckFailedException", "Message": "no"}}, "UpdateItem")

    assert ChecksStore(FakeClient(failed), "t").lease("x" * 26, NOW) is None
    assert ChecksStore(FakeClient(attributes={"status": "queued"}), "t").lease("x" * 26, NOW) == {"status": "queued"}
    assert ChecksStore(FakeClient(_cancelled({"Code": "ConditionalCheckFailed"}, NONE)), "t").settle("x" * 26, "2026-10", 1, 1, "now") is False
    assert ChecksStore(FakeClient(failed), "t").fail("x" * 26, "deadline", 0, NOW, "now") is False
