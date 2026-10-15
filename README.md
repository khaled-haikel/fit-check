# fit-check

A recruiter pastes a job posting and sees, requirement by requirement, where I
meet it, with the evidence next to each answer.

The backend is serverless on AWS (Lambda and API Gateway HTTP API; Bedrock and
DynamoDB arrive in later stages), described in Terraform and deployed by
GitHub Actions without any stored AWS key. A later stage exposes the same
check as a remote MCP server.

**Current stage:** evidence and search. `POST /search` takes a requirement and
returns the evidence cards closest to it, ranked by cosine similarity of Titan
Text Embeddings V2 vectors. `GET /health` returns
`{"ok": true, "commit": "<sha>"}`. Both are served by the Lambda version behind
the `live` alias.

```bash
curl -s -X POST "$API_URL/search" -H 'content-type: application/json' \
  -d '{"query": "Terraform remote state and keyless GitHub OIDC deploys", "k": 3}'
```

`query` takes 1 to 500 characters and `k` 1 to 10 (default 5). Each result
carries the card's `id`, `kind`, `title`, `summary`, `skills`, `proof`,
`verifiable` (some proof has a URL) and `score`; the response also names the
`index_version` that answered.

## Layout

| Path | Contents |
| --- | --- |
| `api/` | Lambda handler (Python 3.12, arm64, Powertools logging), card and case schemas, search, and the pytest suite |
| `config/models.json` | Embedding model and dimensions, read by the index build, the packaging and Terraform |
| `evidence/` | Evidence cards, one YAML file per card |
| `evals/` | Golden set ([labeling guide](evals/LABELING.md)), retrieval evaluation and its baseline |
| `infra/bootstrap/` | One-time foundation: state bucket, GitHub OIDC provider, CI roles, budget |
| `infra/app/` | Lambda, `live` alias, HTTP API, log group |
| `scripts/` | Content validation, index build, Lambda packaging and the smoke test shared by deploy and rollback |
| `.github/workflows/` | Pull request checks, deploy, rollback |
| `docs/adr/` | Architecture decision records |

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
the embedding model, the index build or the search code. It builds the index
as the deploy does, then, for every requirement with evidence in the `dev`
and `test` cases, searches with the requirement's `text_en` and expects its
cited cards back.

- **recall@k** is the share of a requirement's expected cards that appear in
  the top k, averaged over the requirements; it is reported for k = 1, 3 and 5.
- **MRR** is the mean of 1 / rank of the first expected card.
- A **failure** is a requirement with an expected card outside the top 5. The
  job summary lists each one with its expected cards (and their ranks) and the
  top 3 returned; the `retrieval-eval` artifact holds the full result.

The gate compares recall@5 per split with `evals/baseline/retrieval.json` and
fails the job if any split drops by more than 0.02. Without a baseline the run
only reports. To move the baseline, copy `retrieval-baseline.json` from the
artifact of a reviewed run into `evals/baseline/retrieval.json`.

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
