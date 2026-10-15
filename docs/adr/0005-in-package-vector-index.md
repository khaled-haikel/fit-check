# 0005. Vector index packaged inside the Lambda

- Status: Accepted
- Date: 2026-10-15

## Context

`POST /search` ranks the evidence cards against one requirement at a time. The
corpus is small and changes only through pull requests: 41 cards today, written
by hand in `evidence/`. The project rules out anything billed per idle hour,
and stage 2 adds no database.

Managed vector stores that keep a cluster or an instance running (OpenSearch
Serverless, Aurora with pgvector) bill by the hour while idle. Pay-per-request
stores would add a network hop, more IAM, and a second copy of the cards to
keep in sync, for an index that fits in a few hundred kilobytes.

## Decision

- `scripts/build_index.py` embeds the cards with Titan Text Embeddings V2 (1024
  dimensions, normalized) and writes `index.json`. The file has no date, and
  `index_version` is a sha256 of its content (minus the vectors), so the same
  cards give the same version on any machine. The version computed on Windows
  and on the Linux runner matched.
- `scripts/build_lambda.py` puts the index inside the deployment package. The
  Lambda loads it once per cold start and ranks in pure Python, with no extra
  dependency. Every response names the `index_version` that answered.
- Pull request plans package a placeholder, because the plan role cannot call
  Bedrock. The Lambda refuses to search with it, and the deploy refuses to
  package it.
- The cards change only through a deploy. A rollback returns to the cards that
  version was built with.

## Measured: v0

v0 is the first deployed version: one vector per card (title, summary, claims
and skills) and cosine similarity over all 41 cards, gap cards included. The
full record, with its provenance, is `evals/results/retrieval-v0.json`.

| | |
| --- | --- |
| Cards / vectors | 41 / 41 |
| `index.json` | 426,717 bytes (floats with 6 decimals) |
| Search, GitHub runner | p50 1.52 ms, p95 1.59 ms |
| Query embedding, runner → Bedrock us-east-1 | p50 730 ms, p95 2,406 ms |
| Deploy smoke query, Lambda version 6 | `fitcheck-keyless-cicd` 0.567, `fitcheck-terraform-iac` 0.386, `fitcheck-aws-serverless` 0.192 |

The runner timings include the network from GitHub's runners to us-east-1, so
they are not the Lambda's latency. The Lambda logs `embedding_ms` and
`search_ms` on every request; those numbers still have to be collected.

Retrieval quality of v0, as measured on 2026-10-15:

| split | requirements | recall@1 | recall@3 | recall@5 | MRR |
| --- | ---: | ---: | ---: | ---: | ---: |
| dev | 98 | 0.383 | 0.527 | 0.679 | 0.648 |
| test | 77 | 0.294 | 0.444 | 0.545 | 0.540 |

These numbers used the definitions of that run: every requirement with
evidence, whatever its verdict, and every cited card expected, gap cards
included. The later evaluation measures positive evidence and gaps separately,
so its v0 row uses fewer requirements and does not reproduce this table. The
v0 numbers were recorded as a result, not as a baseline: the gate starts from
the retrieval that replaces it.

## Consequences

- Search costs one Bedrock call (the query embedding) and a linear scan of the
  vectors. Nothing runs between requests.
- Changing a card takes a pull request and a deploy, and the evaluation runs
  on that pull request before the change goes live.
- The cold start parses the whole index, so its size shows up in the first
  request after each scale-out.

## When to switch

Move the index out of the package if any of these happens:

- The corpus grows to thousands of chunks, or `search_ms` p95 in the Lambda
  logs passes about 100 ms.
- The package approaches Lambda's limits (50 MB zipped for a direct upload,
  250 MB unzipped), or the index parse dominates the cold start.
- Cards need to change without a deploy, or each user or tenant needs an index
  of their own.
- Search needs filters richer than the card kind and the requirement category.
