# 0002. Terraform state in S3 with native locking

- Status: Accepted
- Date: 2026-10-13

## Context

Two Terraform configurations need remote state: `infra/bootstrap`, applied by
hand in AWS CloudShell (whose storage is wiped after a period of inactivity),
and `infra/app`, applied by CI. The state must survive both, must not put
account identifiers in the public repository, and concurrent runs must not
corrupt it. The whole project has a US$ 3 monthly budget.

## Decision

- One S3 bucket, `fitcheck-tfstate-<8 random hex characters>`, with
  versioning, SSE-S3 encryption, all public access blocked, ACLs disabled and
  `prevent_destroy`. The random suffix replaces the account ID that usually
  makes state bucket names unique.
- Keys `bootstrap/terraform.tfstate` and `app/<env>/terraform.tfstate`. The CI
  roles reach only `app/*`: the bootstrap state, which defines the CI's own
  permissions, is administrator-only.
- Locking with `use_lockfile = true`: Terraform writes `<key>.tflock` next to
  the state with a conditional write. S3-native locking shipped in Terraform
  1.10 and became generally available in 1.11, which deprecated the DynamoDB
  arguments. The configurations require `>= 1.11`; CloudShell and CI pin
  1.16.5.
- Pull request plans run with `-lock=false`, so the plan role stays read-only.
  A plan never writes state, so skipping the lock cannot corrupt it; at worst,
  a plan reads state that a running apply is about to change.
- Partial backend configuration: the bucket name comes from `-backend-config`,
  through a git-ignored `backend.hcl` in CloudShell and the `TF_STATE_BUCKET`
  repository variable in CI.
- The bootstrap creates the bucket that holds its own state. Its first run
  uses a local backend through a git-ignored `backend_override.tf`; right after
  the apply, in the same CloudShell session, `terraform init -migrate-state`
  moves the state into the bucket.

## Alternatives considered

- **DynamoDB lock table.** Deprecated by Terraform since 1.11; one more
  resource and one more set of permissions, for nothing the lock file does not
  already do.
- **HCP Terraform.** Another account and credential to manage, and a second
  place where state lives.
- **Local or committed state.** Lost when CloudShell storage is wiped,
  unreachable from CI, and it would publish account data.
- **Customer-managed KMS key.** About US$ 1 a month, a third of the budget,
  plus a key grant for every role, for no requirement that SSE-S3 does not
  meet.
