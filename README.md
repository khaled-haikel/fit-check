# fit-check

A recruiter pastes a job posting and sees, requirement by requirement, where I
meet it, with the evidence next to each answer.

**Try it:** [khaled-haikel.github.io/fit-check](https://khaled-haikel.github.io/fit-check/)

The backend is serverless on AWS (Lambda, API Gateway HTTP API, Bedrock,
DynamoDB and SQS), described in Terraform and deployed by GitHub Actions
without any stored AWS key. A later stage exposes the same check as a remote
MCP server.

**Current stage:** the public page, a static site on GitHub Pages that calls
the analysis API ([ADR 0008](docs/adr/0008-static-page-on-github-pages.md)).

`POST /checks` takes a job description and returns an ID; `GET /checks/{id}`
returns, requirement by requirement, the verdict and the evidence it cites
(below, and [ADR 0006](docs/adr/0006-async-analysis.md)).

`POST /search` takes a requirement and returns the evidence cards that best
support it, plus the closest gap card.
Cards are ranked by a hybrid search, routed by the requirement's category: one
Titan Text Embeddings V2 vector per card and BM25 over the claims, fused by
Reciprocal Rank Fusion ([ADR 0005](docs/adr/0005-in-package-vector-index.md)).
`GET /health` returns `{"ok": true, "commit": "<sha>"}`. Both are served by the
Lambda version behind the `live` alias.

```bash
curl -s -X POST "$API_URL/search" -H 'content-type: application/json' \
  -d '{"query": "Terraform remote state and keyless GitHub OIDC deploys", "k": 3, "category": "skill"}'
```

- `query` takes 1 to 500 characters, and `k` takes 1 to 10 (default 5).
- `category` is optional. It takes one of the requirement categories: `skill`,
  `experience`, `education`, `language`, `location`, `domain` or `soft`.
  `location` and `language` search only profile cards. `education` searches
  education and profile cards. `experience` searches project, role, skill and
  profile cards. The other categories search project, role and skill cards.
- `results` holds positive evidence only, ordered by `score` (the fused
  score). Each result carries the card's `id`, `kind`, `title`, `summary`,
  `skills`, `proof`, `verifiable` (some proof has a URL), `score`, and
  `similarity` (the cosine similarity between the query and the card).
- `gap` is the closest gap card, in the same shape, so a gap never takes the
  place of evidence.
- `index_version` names the index that answered.

## Analysing a job description

```bash
curl -s -X POST "$API_URL/checks" -H 'content-type: application/json' \
  --data "$(jq -Rs '{job_description: .}' job.txt)"
# {"check_id": "...", "status": "queued", "poll_after_ms": 3000}

curl -s "$API_URL/checks/<check_id>"
```

What happens to a job description:

1. Claude Sonnet 4.6 on Amazon Bedrock extracts its requirements. Each one
   keeps the excerpt it came from.
2. The search finds evidence for each requirement.
3. Claude judges each requirement `meets`, `partial` or `does_not_meet`, and
   the code checks every citation against the card it quotes. A verdict
   without a valid citation becomes `does_not_meet`.
4. The code computes a score from 0 to 100, weighting must requirements twice
   as much as nice ones.

`GET` shows the requirements as soon as they are extracted, and the verdicts
when `status` is `done`. A failed check carries an `error` code
([runbook](docs/runbook.md)).

Limits:

- The job description must have 200 to 20,000 characters after normalization
  (a `400` otherwise).
- One IP may start 5 new analyses per UTC day (`429 daily_limit`). The same
  job description under the same `analysis_version` gets the same ID, and is
  answered from the table without counting.
- Public analyses may spend 2 USD a month (`429 monthly_budget`, or `busy`
  while analyses in flight hold the rest), and one analysis at most 0.50 USD.
- `POST /checks` allows 1 request per second (burst 2), and `GET /checks/{id}`
  allows 5 (burst 10).

Job descriptions and their results are kept for 30 days, then expire. Client
IPs are kept only as a hash, for the daily limit, and are never logged.

## Layout

| Path | Contents |
| --- | --- |
| `api/` | Lambda handler (Python 3.12, arm64, Powertools logging): card and case schemas, search, the analysis (`analysis/`), the checks API and worker (`checks/`), and the pytest suite |
| `prompts/` | The extraction and judgment prompts and the few-shot examples |
| `config/` | `models.json`: embedding and generation models, read by the index build, the packaging and Terraform. `retrieval.json`: the retrieval settings the Lambda serves. `analysis.json`: the analysis settings and limits. `prices.json`: model prices |
| `evidence/` | Evidence cards, one YAML file per card |
| `evals/` | Golden set ([labeling guide](evals/LABELING.md)), retrieval and analysis evaluations, their baselines and past results |
| `infra/bootstrap/` | One-time foundation: state bucket, GitHub OIDC provider, CI roles, budget |
| `infra/app/` | Lambda, `live` alias, HTTP API, log group, the checks table, queue and dead-letter queue, the alarm and its topic |
| `scripts/` | Content validation, index build, Lambda packaging and the smoke test shared by deploy and rollback |
| `.github/workflows/` | Pull request checks, deploy, rollback, the page's checks and its publication on GitHub Pages |
| `page/` | The public page (Vite, React, TypeScript, Tailwind), its tests and its recorded examples |
| `docs/adr/` | Architecture decision records |
| `docs/runbook.md` | Failed analyses, the dead-letter queue, the alarm, the budget |

## How the infrastructure comes up

1. **Once, by hand:** the account administrator applies `infra/bootstrap` in
   AWS CloudShell ([runbook](infra/bootstrap/README.md)). It creates the
   Terraform state bucket, the GitHub OIDC provider and the CI roles.
2. **Every pull request:** `terraform fmt -check`, `terraform validate`,
   offline `terraform test` of both configurations, pytest, and a read-only
   `terraform plan` of `infra/app`. Changes to cards or cases also run the
   content validation and the retrieval evaluation (below).
3. **Every push to main:** pytest, then the eval role builds the search index
   with Titan, the package is built around it, the apply role runs
   `terraform apply`, and a smoke test calls `/health` (expecting the pushed
   commit) and `/search` (expecting a known card in the top 3).

The workflows read four repository variables with role ARNs and the bucket,
outputs of `infra/bootstrap`: `AWS_PLAN_ROLE_ARN`, `AWS_APPLY_ROLE_ARN`,
`AWS_EVAL_ROLE_ARN` and `TF_STATE_BUCKET`; plus `ALLOWED_ORIGIN`, the only
origin CORS accepts.

## Why there is no stored key

GitHub Actions requests a short-lived OIDC token, and AWS STS exchanges it for
one-hour credentials of a role that trusts only this repository: pull
requests get the read-only plan role, the main branch gets the apply role,
and both may use the eval role, which can only invoke the Bedrock models.
Nothing long-lived exists to leak, rotate or revoke, and the bootstrap runs in
CloudShell, so no access key exists on a workstation either. See
[ADR 0001](docs/adr/0001-keyless-oidc.md) and
[ADR 0002](docs/adr/0002-s3-state-native-lock.md).

## Rolling back

1. Find the version to return to: every Deploy run prints the version it
   published, and `aws lambda list-versions-by-function --function-name fitcheck-api`
   lists them all.
2. In GitHub, open **Actions → Rollback → Run workflow**, keep the `main`
   branch, and enter the version number.
3. The workflow points the `live` alias at that version and runs the smoke
   test, which expects the commit that version was built from.

The rollback lasts until the next deploy, which moves `live` back to the
version it publishes. To keep the old code, revert the commit on main
([ADR 0003](docs/adr/0003-alias-rollback.md)).

Each version carries the search index it was deployed with, so a rollback also
returns to that version's cards. Versions from before `/search` existed answer
it with 404; for them the smoke test checks only `/health`.

## Evidence cards

A card is one YAML file in `evidence/`, named after its `id`:

```yaml
id: example-project            # kebab-case, equal to the file name
kind: project                  # project, role, skill, profile, education or gap
title: Short title
period: 2025-03/present        # optional: YYYY, YYYY-MM, YYYY-MM/YYYY-MM or YYYY-MM/present
summary: One to three sentences.
claims:                        # one to eight
- One concrete, checkable statement.
skills: [python, aws]          # lowercase
proof:                         # at least one
- type: repo                   # repo, live, demo, doc or none
  url: https://github.com/...  # optional, https only
  note: Optional context.
```

A card is `verifiable` when some proof has a URL; that is derived, never
written in the file. To add one:

1. Write the file and run `python scripts/validate_content.py`.
2. Review the cases whose requirements the card supports and cite it in their
   `evidence` ([labeling guide](evals/LABELING.md)).
3. Open a pull request. The retrieval evaluation shows how the numbers move;
   the deploy after the merge rebuilds the index, so the card is searchable
   once it is live.

## Retrieval evaluation

`evals/retrieval.py` runs on pull requests that touch the cards, the cases,
`config/`, the index build or the search code. It builds the index as the
deploy does, then embeds the chunks that index leaves out but other
configurations rank with. It searches with each `dev` and `test` requirement's
`text_en` and its category, through the same `search()` function the Lambda
serves. It measures two channels:

- **Evidence channel:** `meets` and `partial` requirements. The expected cards
  are the ones they cite, minus gap cards. The metrics are:
  - **hit@5**: some expected card is in the top 5.
  - **recall@5**: the share of the expected cards in the top 5.
  - **MRR**: the mean of 1 / rank of the first expected card.
- **Gap channel:** requirements that cite a gap card. The metrics are
  **hit@1** and **hit@3** among the gap results.

All metrics are averaged over requirements.

Every configuration in the script's `ABLATION` list (v0, +chunks, +hybrid,
+routing, +routing with whole-card vectors) is scored on the same index and
the same query embeddings. The job
summary shows the ablation table. It also lists, for the served configuration,
each failure (an expected card outside the top 5) with the expected cards'
ranks and the top 3 returned. The `retrieval-eval` artifact holds the full
result.

The gate compares hit@5 and recall@5 of the served configuration, per split,
with `evals/baseline/retrieval.json`. It fails the job if any of them drops by
more than 0.02.

Choose configurations on `dev` only. `--splits dev` keeps `test` from even
being embedded; `test` is for the final table. To move the baseline, copy
`retrieval-baseline.json` from the artifact of a reviewed run into
`evals/baseline/retrieval.json`. Earlier results stay in `evals/results/`.

## Analysis evaluation

`evals/analysis.py` runs on pull requests that touch the prompts, the
analysis settings, the evaluation, the golden set, the cards or the analysis
code. It calls the same functions the worker runs, in three modes:

- **extract:** extraction on each dev job description, paired with the
  labeled requirements;
- **judge:** judgment on the labeled requirements;
- **e2e:** the whole pipeline on the special cases (language, prompt
  injection, off domain).

It gates against `evals/baseline/analysis.json`, with tolerances from a
three-repetition calibration. Model responses are cached between the runs of
a pull request, and each run is capped at a few dollars. The test split runs
only by hand, at the end of a stage, through `eval-analysis-test.yml`. The
modes, the pairing threshold, the gate and the numbers are in
[ADR 0007](docs/adr/0007-analysis-evaluation.md).

Results on the test split, measured once at the end of stage 3
(`evals/results/analysis-test-2026-10-21.json`):

| Metric | Test | Dev |
| --- | ---: | ---: |
| judgment accuracy | 0.804 | 0.894 |
| overestimation (a verdict above the label) | 0.112 | 0.049 |
| severe overestimation (meets on does_not_meet) | 0.000 | 0.000 |
| extraction recall | 0.925 | 0.947 |
| extraction precision | 0.892 | 0.890 |
| e2e accuracy, paired requirements | 0.828 | |

## Local development

```powershell
cd api
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[test]"
.venv\Scripts\python -m pytest
```

From the repository root, with that environment:

- `python scripts/validate_content.py` checks the cards and cases, without AWS.
- `python scripts/build_lambda.py --placeholder-index` builds
  `dist/fitcheck-api.zip` with a placeholder index, enough for a plan; that
  package answers `/search` with 503. CI builds the real index with
  `scripts/build_index.py`, which needs Bedrock access, and passes it with
  `--index`.

### The page

```bash
cd page
npm ci
npm test
VITE_API_URL=https://<api-id>.execute-api.us-east-1.amazonaws.com npm run dev
```

`npm run dev` serves it at `http://localhost:5173/fit-check/`. The API
accepts browser calls only from the Pages origin, so a local page shows the
examples but cannot start an analysis. `npm run build` lints, checks the types
and writes `page/dist/`, which `.github/workflows/pages.yml` publishes from
main. `npm run refresh-examples`, with `API_URL` set, records the two examples
again through the public API; each one is a paid analysis unless the API has
it cached.
