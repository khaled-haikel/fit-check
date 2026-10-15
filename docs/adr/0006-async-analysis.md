# 0006. Asynchronous analysis on a queue

- Status: Accepted
- Date: 2026-10-15

## Context

An analysis extracts a job description's requirements with Claude Sonnet
4.6, retrieves evidence for each one and judges them all (ADR 0004 and ADR
0007). In calibration a judgment took 20 s at p50 and 48 s at p95, plus about
4 s of extraction and the Titan calls. That is more than API Gateway's 30 s
integration timeout allows for a synchronous request.

The project rules out anything billed per idle hour. Public use must not be
able to run up the Bedrock bill, and nothing may claim more than the evidence
cards support.

## Decision

### One Lambda, one queue, one table

- `POST /checks` validates the job description, writes the check and queues
  its ID on the SQS standard queue `fitcheck-checks`.
- The same Lambda consumes the queue through its `live` alias, one message at
  a time and at most two at once. A rollback therefore rolls back the worker
  with the API.
- Timing:
  - the function timeout is 300 s;
  - the queue's visibility timeout is 1,800 s, six times the function timeout,
    as AWS recommends;
  - a message that fails twice moves to `fitcheck-checks-dlq`, which keeps it
    for 14 days. A CloudWatch alarm on that queue emails the address in the
    `ALERT_EMAIL` secret through the SNS topic `fitcheck-alerts`.
- One on-demand DynamoDB table, `fitcheck-checks`, without point-in-time
  recovery or indexes, holds three kinds of item:
  - `check#<id>`: the normalized job description, progress, result and cost.
    Its TTL is 30 days, so job descriptions are kept for 30 days.
  - `rate#<ip hash>#<day>`: the new analyses one client started in a UTC day.
    Its TTL is 2 days. The IP is stored only as the first 16 hex characters of
    its sha256, and never logged.
  - `spend#<month>`: `committed`, `reserved` and `settled`, in whole
    micro-dollars. No float is ever written.

### The ID is the cache

The check ID is the first 26 characters of the lowercase base32 of
sha256(`analysis_version` + newline + the normalized job description). The
same posting under the same prompts, model, index and code always gets the
same ID. A second request for it is answered from the table and costs
nothing. Any change to what an analysis depends on changes
`analysis_version`, and with it every ID.

A check that is not final and has not moved for 15 minutes counts as failed,
both for `POST`, which may then run it again, and for `GET`, which reports
`timed_out` without writing.

### Limits with reservation and settlement

These limits hold in code, with conditional writes:

- **Job description:** from 200 to 20,000 characters after normalization.
- **Per client:** 5 new analyses per IP per UTC day. A cached answer does not
  count.
- **Per analysis:** at most 0.50 USD, enforced before every model call from
  the worst case (the input at the cache-write price plus `max_tokens` at the
  output price).
- **Public budget:** 2 USD per month.

`POST` reserves the per-analysis cap inside one `TransactWriteItems` with
three operations:

1. the put of the check;
2. the update of the client's daily counter;
3. the update of the month's spend.

A condition cannot add, so the code computes the room (monthly cap minus the
per-analysis cap) and the spend condition is `committed <= room`. The
transaction uses only Put and Update; ConditionCheck would need an action the
workload boundary does not grant.

When the transaction is cancelled, the old items DynamoDB returns tell the
cause:

- an existing check: the cached answer;
- the daily counter: `429 daily_limit`;
- the spend: `429 monthly_budget` if the settled spend alone fills the
  month, `busy` if only reservations do.

When a check reaches a final state, one transaction settles it. It releases
the reservation, adds the real cost to `settled` and moves `committed` by the
difference. It runs only once per check, guarded by `settled_at`.

A crash between the reservation and the settlement leaves the reservation
in place until it is fixed by hand (`docs/runbook.md`).

### Phases, resume and failing closed

The worker works one phase at a time and saves after each one:

1. extraction, then the status becomes `retrieving`;
2. retrieval, then `judging`;
3. judgment, validation and score, then `done`.

A redelivered message resumes from the last saved phase. A conditional lease
(`lease_until`) stops two invocations from running the same check.

Expected failures mark the check `failed` with a code, settle it and return
success to the queue:

- a Bedrock error;
- a model reply that is still invalid after the one retry;
- a refusal;
- the per-analysis cap;
- the deadline: less than 135 s left before a model call, that is the 120 s
  read timeout plus 15.

Anything else raises, so SQS retries it and then dead-letters it. The code
checks every citation (ADR 0007), and a verdict left without a valid citation
becomes does_not_meet. The judge never sees the job description, so
instructions injected into a posting can reach only the extraction.

## Costs

Measured in the third calibration (ADR 0007), at the prices in
`config/prices.json`:

| Item | Cost |
| --- | --- |
| One analysis, end to end | 0.03 to 0.09 USD (Claude; Titan is negligible) |
| One cached analysis | 0 |
| Deploy canary | One analysis each time `analysis_version` changes, from the public budget |
| SQS polling while idle | About 650,000 ReceiveMessage a month, within the 1 million free requests |
| DynamoDB, on demand | A few reads and writes per analysis, cents a month |
| CloudWatch alarm | 1 standard alarm, within the free 10 |
| SNS email | Within the free 1,000 emails |
| Lambda | About 30 to 60 s at 256 MB per analysis, within the free tier |

Public analyses can cost at most 2 USD a month (`public_monthly_cap_usd`).
Pull request evaluations are billed separately (ADR 0007).

The figures measured in production, per analysis and per month, are added
after the first deploy of this stage.

## Alternatives considered

- **Synchronous within 30 s.** The judgment alone reaches 48 s at p95, and a
  timeout would charge for a result nobody receives.
- **Asynchronous Lambda invocation.** It has no visible queue, no redrive, and
  no per-message concurrency control that a rollback moves with the alias.
- **Step Functions.** It is billed per state transition and adds a second
  place to see failures, for three sequential steps that resume from the
  table anyway.
- **DynamoDB Streams.** It would start work from writes, but cannot retry one
  analysis or bound concurrency per message as simply. It also ties the
  worker to every change of the table.
- **Structured output.** The 5.x models reject the parameters it needs on
  Bedrock (ADR 0004). The JSON in the text is validated in code instead.

## Switching to a 5.x model

- **Thinking is on by default** on Sonnet 5 and 5.5, and
  `thinking: {"type": "disabled"}` is a 400. The extraction omits the field,
  so on 5.x it would think and spend output tokens. Measure it before
  lowering `max_tokens`.
- **What the switch changes:** `config/models.json`, the IAM statements that
  `generation_model_id` drives, and `analysis_version`. A new
  `analysis_version` makes every check ID new and empties the cache.
- **What must run first:** calibrate again before the baseline of ADR 0007
  is compared.

## Consequences

- The alert email is a sensitive Terraform variable that GitHub masks. It is
  stored in plain text in the Terraform state, in the private state bucket.
- A rollback to a version from before this stage cannot consume the queue.
  Its messages fail twice, reach the dead-letter queue and raise the alarm.
- The email subscription works only after the address confirms it.
