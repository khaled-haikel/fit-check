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
keep in sync, for an index of a few megabytes at most.

## Decision

- `scripts/build_index.py` embeds the cards with Titan Text Embeddings V2 (1024
  dimensions, normalized) and writes `index.json`. The file holds the cards,
  the text of every chunk, and the retrieval settings from
  `config/retrieval.json`. It also holds vectors, but only for the chunk kinds
  those settings rank with (`embedded`). It has no date, and `index_version`
  is a sha256 of everything but the vectors, so the same input gives the same
  version on any machine. The version computed on Windows and on the Linux
  runner matched.
- `scripts/build_lambda.py` puts the index inside the deployment package. It
  refuses an index built with other settings than the committed ones. The
  Lambda loads the index once per cold start and ranks in pure Python (cosine
  with `math.sumprod`, BM25), with no extra dependency. Every response names
  the `index_version` that answered.
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

## Measured: retrieval v1

The same index holds every configuration below, each a change on top of v0:

1. **+chunks:** one vector per claim, embedded as `"<title>: <claim>"`, plus
   one for `"<title>: <summary>"`. A card scores its best chunk.
2. **+hybrid:** BM25 in pure Python over the claim chunks, each read with the
   card's skills (k1 1.2, b 0.75). It is fused with the vector ranking by
   Reciprocal Rank Fusion with k = 60. A card that matches no query term gets
   no BM25 rank.
3. **+routing:** the requirement's category decides which card kinds may
   answer:
   - `location` and `language` reach only profile cards.
   - `education` reaches education and profile cards.
   - `experience` reaches project, role, skill and profile cards, because the
     years of experience are stated in a profile card.
   - The other categories reach project, role and skill cards.

   Gap cards never compete with evidence: they are ranked in their own
   channel, and `/search` returns the closest one in `gap`.
4. **+routing (card vectors):** the same BM25 and routing, but with v0's
   whole-card vectors instead of claim chunks. BM25 still reads the claims.

The evaluation measures two channels:

- **Evidence channel:** meets and partial requirements. The expected cards are
  the ones they cite, minus gap cards.
- **Gap channel:** requirements that cite a gap card, among the gap results.

Every configuration is scored on one index and one set of query embeddings.
The selection rule was fixed before any dev number was seen: the highest dev
evidence hit@5, with ties going to recall@5, then MRR, then the configuration
with fewer vectors. BM25, RRF and the tokenizer keep fixed parameters, with no
sweeps.

The choice took two rounds, both decided on dev with test not embedded:

- **Round 1:** the four cumulative rows. +routing won, then without the
  `experience` → profile route. Lambda version 7 serves it, with dev hit@5
  0.883 and recall@5 0.770, and test 0.868 and 0.752.
- **Round 2:** review added the `experience` → profile route and the
  card-vectors variant. The variant won on dev, and only then was test scored.

| configuration | vectors | split | evidence n | hit@1 | hit@3 | hit@5 | recall@5 | MRR | gap n | gap hit@1 | gap hit@3 |
| --- | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| v0 | 41 | dev | 94 | 0.511 | 0.681 | 0.830 | 0.663 | 0.639 | – | – | – |
| +chunks | 185 | dev | 94 | 0.457 | 0.660 | 0.734 | 0.578 | 0.594 | – | – | – |
| +hybrid | 185 | dev | 94 | 0.511 | 0.755 | 0.830 | 0.713 | 0.657 | – | – | – |
| +routing | 185 | dev | 94 | 0.617 | 0.830 | 0.883 | 0.782 | 0.736 | 5 | 1.000 | 1.000 |
| **+routing (card vectors)** | 41 | dev | 94 | 0.670 | 0.851 | **0.915** | 0.800 | 0.781 | 5 | 1.000 | 1.000 |
| v0 | 41 | test | 76 | 0.408 | 0.566 | 0.658 | 0.539 | 0.536 | – | – | – |
| +chunks | 185 | test | 76 | 0.355 | 0.513 | 0.671 | 0.546 | 0.491 | – | – | – |
| +hybrid | 185 | test | 76 | 0.421 | 0.711 | 0.829 | 0.717 | 0.586 | – | – | – |
| +routing | 185 | test | 76 | 0.474 | 0.816 | 0.895 | 0.789 | 0.652 | 4 | 0.750 | 1.000 |
| **+routing (card vectors)** | 41 | test | 76 | 0.513 | 0.829 | 0.895 | 0.796 | 0.679 | 4 | 0.750 | 1.000 |

v0, +chunks and +hybrid do not route, so the change to the `experience` route
leaves their rows as they were in round 1.

What the table shows:

- **Served configuration:** +routing (card vectors), written in
  `config/retrieval.json` as
  `{"chunks": "card", "hybrid": true, "rrf_k": 60, "routing": true}`. Its
  baseline in `evals/baseline/retrieval.json` is hit@5 0.915 and recall@5
  0.800 on dev, and 0.895 and 0.796 on test. The gate tolerance is 0.02 on
  each.
- **Claim chunks did not pay for themselves.**
  - Alone, they lowered dev hit@5 from 0.830 to 0.734.
  - With BM25 and routing, whole-card vectors beat them on dev (hit@5 0.915
    against 0.883), with 41 vectors instead of 185.
  - On test, hit@5 ties at 0.895, and whole-card vectors stay slightly ahead
    on recall@5 and MRR.
- **The routing ceiling is down to one card.** With `experience` reaching
  profile cards, one expected card in dev is still out of reach:
  `profile-languages`, under `soft`. Every dev requirement has at least one
  reachable card.
- **The gap channel covers only 5 dev and 4 test requirements.** Its numbers
  are a sanity check, not a measurement.

Cost of the served configuration:

| | |
| --- | --- |
| Vectors in the packaged index | 41, one per card. The text of all 226 chunks stays in it for BM25. |
| `index.json` | 496,143 bytes (2,271,992 while it also held the 185 chunk vectors) |
| Index load, laptop | about 76 ms with 226 vectors; less with 41 |
| Search, GitHub runner | p50 0.44 to 0.77 ms, p95 0.49 to 0.85 ms across runs (41 vectors and BM25 over 144 claims) |
| Query embedding, runner → Bedrock | p50 0.9 to 1.1 s, p95 about 1.6 s |
| Index build in the deploy | 6 s (41 calls) |

The account's Titan quota looks like about 70 requests a minute. The v0 run
made 41 calls in 9 s and then held about 70 a minute with backoff. A first try
with four parallel calls ran out of retries on `ThrottlingException`. CI
therefore embeds one call at a time, with botocore's adaptive retry mode.

The deploy embeds only the 41 whole-card chunks. The evaluation embeds the
other 185 (title and summary, claims) for the ablation rows that rank with
them, rounded the same way as the index stores vectors. All ten rows matched
the numbers measured when the index held every vector.

## Consequences

- Search costs one Bedrock call (the query embedding) and a linear scan of the
  vectors. Nothing runs between requests.
- Changing a card takes a pull request and a deploy, and the evaluation runs
  on that pull request before the change goes live.
- The cold start parses the whole index, so its size shows up in the first
  request after each scale-out.
- `/search` takes an optional `category`, so callers that know the
  requirement's category (stage 3's extraction) get routed results.

## When to switch

Move the index out of the package if any of these happens:

- The corpus grows to thousands of chunks, or `search_ms` p95 in the Lambda
  logs passes about 100 ms.
- The package approaches Lambda's limits (50 MB zipped for a direct upload,
  250 MB unzipped), or the index parse dominates the cold start.
- Cards need to change without a deploy, or each user or tenant needs an index
  of their own.
- Search needs filters richer than the card kind and the requirement category.
