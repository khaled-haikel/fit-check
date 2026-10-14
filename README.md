# fit-check

A recruiter pastes a job posting and sees, requirement by requirement, where I
meet it, with the evidence next to each answer.

The backend is serverless on AWS (Lambda and API Gateway HTTP API; Bedrock and
DynamoDB arrive in later stages), described in Terraform and deployed by
GitHub Actions without any stored AWS key. A later stage exposes the same
check as a remote MCP server.

**Current stage:** the foundation. `GET /health` returns
`{"ok": true, "commit": "<sha>"}`, served by the Lambda version behind the
`live` alias.

## Layout

| Path | Contents |
| --- | --- |
| `api/` | Lambda handler (Python 3.12, arm64, Powertools logging) and its pytest suite |
| `infra/bootstrap/` | One-time foundation: state bucket, GitHub OIDC provider, CI roles, budget |
| `infra/app/` | Lambda, `live` alias, HTTP API, log group |
| `scripts/` | Lambda packaging and the smoke test shared by deploy and rollback |
| `.github/workflows/` | Pull request checks, deploy, rollback |
| `docs/adr/` | Architecture decision records |

## How the infrastructure comes up

1. **Once, by hand:** the account administrator applies `infra/bootstrap` in
   AWS CloudShell ([runbook](infra/bootstrap/README.md)). It creates the
   Terraform state bucket, the GitHub OIDC provider and the two CI roles.
2. **Every pull request:** `terraform fmt -check`, `terraform validate`,
   offline `terraform test` of both configurations, pytest, and a read-only
   `terraform plan` of `infra/app`.
3. **Every push to main:** pytest, Lambda packaging, `terraform apply`, and a
   smoke test that calls `/health` and expects the pushed commit.

The workflows read three repository variables: `AWS_PLAN_ROLE_ARN`,
`AWS_APPLY_ROLE_ARN` and `TF_STATE_BUCKET` (outputs of `infra/bootstrap`),
plus `ALLOWED_ORIGIN`, the only origin CORS accepts.

## Why there is no stored key

GitHub Actions requests a short-lived OIDC token, and AWS STS exchanges it for
one-hour credentials of a role that trusts only this repository: pull
requests get the read-only plan role, the main branch gets the apply role.
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

## Local development

```powershell
cd api
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[test]"
.venv\Scripts\python -m pytest
```

`python scripts/build_lambda.py` builds `dist/fitcheck-api.zip`, the package
CI deploys.
