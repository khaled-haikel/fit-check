# Runbook

Commands run in AWS CloudShell, in us-east-1. Names are the production ones;
other environments add a suffix (`fitcheck-checks-staging`). Amounts in the
`spend#` items are whole micro-dollars (1,000,000 = 1 USD).

## An analysis failed

`GET /checks/{id}` returns `"status": "failed"` and an `error` code. The
codes:

| Code | Meaning |
| --- | --- |
| `deadline` | The invocation would not have had time for the next model call. |
| `cost_cap` | The next model call could have taken the analysis past its cap. |
| `invalid_model_output` | The model's reply was still unusable after the one retry. |
| `model_refused` | The model refused. |
| `bedrock_error` | Bedrock failed (throttling after the retries, access, an outage). |
| `enqueue_failed` | The queue rejected the message; nothing ran and the reservation was released. |
| `unavailable` | The deployed package cannot run analyses (placeholder index or model mismatch). |
| `timed_out` | The check stopped moving for 15 minutes; a new `POST` runs it again. |

The worker logs with the check ID as `correlation_id`. In CloudWatch Logs
Insights, choose the log group `fitcheck-api` and run:

```
fields @timestamp, message, phase, attempt, stop_reason, cost_usd, code, detail
| filter correlation_id = "<check id>"
| sort @timestamp asc
```

The logs never hold the job description; to find a check from the posting,
compute its ID or look for `job_description_sha256`.

## The dead-letter queue and its alarm

The alarm `fitcheck-checks-dlq-messages` emails the alert address when a
message is in `fitcheck-checks-dlq`. That means an analysis failed twice with
an unexpected error, which an expected error would have reported as
`failed`. Find the cause in the logs first, with the check ID from the
message:

```bash
dlq_url="$(aws sqs get-queue-url --queue-name fitcheck-checks-dlq --query QueueUrl --output text)"
aws sqs receive-message --queue-url "$dlq_url" --max-number-of-messages 10 --visibility-timeout 0
```

Once the cause is fixed, move the messages back to the main queue:

```bash
dlq_arn="$(aws sqs get-queue-attributes --queue-url "$dlq_url" --attribute-names QueueArn --query Attributes.QueueArn --output text)"
aws sqs start-message-move-task --source-arn "$dlq_arn"
aws sqs list-message-move-tasks --source-arn "$dlq_arn"
```

The alarm returns to OK on its own once the queue is empty.

`aws cloudwatch describe-alarms --alarm-names fitcheck-checks-dlq-messages`
shows its state. If no email arrives, check that the subscription was
confirmed:

```bash
aws sns list-subscriptions --query "Subscriptions[?contains(TopicArn, 'fitcheck-alerts')]"
```

## The public budget

Public analyses may spend `public_monthly_cap_usd` per month (default 2 USD).
To raise it, or to set it to 0 and stop new analyses, change the variable's
default in `infra/app/variables.tf` in a pull request; the deploy applies it.
With 0, every new `POST /checks` answers `429 monthly_budget`, cached results
are still served, and the deploy's canary only warns.

To see the month's spend:

```bash
aws dynamodb get-item --table-name fitcheck-checks \
  --key '{"pk": {"S": "spend#2026-10"}}' --consistent-read
```

`committed` = `settled` (spent) + `reserved` (analyses in flight). For the
whole Bedrock bill, including the pull request evaluations, use Cost Explorer
filtered by the Amazon Bedrock service.

## A reservation stuck by a crash

A check reserves 500,000 micro-dollars and releases them when it settles. If
the worker crashed in a way that never settled (the message reached the
dead-letter queue and was not moved back), `reserved` stays too high and new
analyses get `429 busy`. Find the check (status not `done`, no `settled_at`)
from the logs, then release its reservation and mark it settled in one
transaction, with the month of its `spend_month` and the cost it recorded
(`cost_micro`):

```bash
aws dynamodb transact-write-items --transact-items '[
  {"Update": {"TableName": "fitcheck-checks", "Key": {"pk": {"S": "check#<check id>"}},
    "UpdateExpression": "SET settled_at = :at, #status = :failed, #error = :code",
    "ConditionExpression": "attribute_not_exists(settled_at)",
    "ExpressionAttributeNames": {"#status": "status", "#error": "error"},
    "ExpressionAttributeValues": {":at": {"S": "manual"}, ":failed": {"S": "failed"}, ":code": {"S": "timed_out"}}}},
  {"Update": {"TableName": "fitcheck-checks", "Key": {"pk": {"S": "spend#<YYYY-MM>"}},
    "UpdateExpression": "ADD #reserved :release, #settled :cost, #committed :difference",
    "ExpressionAttributeNames": {"#reserved": "reserved", "#settled": "settled", "#committed": "committed"},
    "ExpressionAttributeValues": {":release": {"N": "-500000"}, ":cost": {"N": "<cost_micro>"}, ":difference": {"N": "<cost_micro - 500000>"}}}}
]'
```

## Rolling back

The Rollback workflow moves the `live` alias, and the queue's event source
mapping follows it. A version from before stage 3 has no worker: queued
messages fail twice, go to the dead-letter queue and raise the alarm. After
rolling forward again, move them back as above. The smoke test of a rollback
runs the analysis canary only on versions that have `GENERATION_MODEL_ID`.
