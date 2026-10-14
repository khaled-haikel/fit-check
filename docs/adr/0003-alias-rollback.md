# 0003. Roll back by moving the live alias

- Status: Accepted
- Date: 2026-10-13

## Context

A bad deploy should be undone in about a minute, without rebuilding anything
and without waiting for a pull request. Lambda versions are immutable
snapshots of code and configuration, so an earlier one stays deployable for
as long as it exists.

## Decision

- Every deploy publishes a version. `GIT_COMMIT` is part of the function's
  configuration and changes on every push to main, so Terraform publishes a
  new version and points the `live` alias at it in the same apply. Each version
  keeps the commit it was built from.
- API Gateway integrates with `live`, never with `$LATEST`, and the invoke
  permission is granted on the alias.
- The Rollback workflow (`workflow_dispatch`, main branch only, apply role)
  takes a version number, reads the `GIT_COMMIT` stored in that version, moves
  `live` with `aws lambda update-alias`, and runs the same smoke test as the
  deploy, expecting that commit from `/health`.
- **A manual rollback lasts only until the next deploy.** Terraform still
  declares `live` → newest version, so the next push to main moves the alias
  back to the version that deploy publishes. While a rollback is active, pull
  request plans show the alias drift. To make a rollback stick, revert the
  faulty commit on main: the deploy then publishes the reverted code as a new
  version.
- Deploys and rollbacks share one concurrency group, so they never interleave.
- Old versions stay as rollback targets. At about 1 MB each, they are far
  from Lambda's 75 GB code storage quota.

## Alternatives considered

- **Revert and redeploy only.** Durable, but it runs the whole pipeline
  (tests, package, apply) when the goal is to stop the damage now. It remains
  the way to make a rollback permanent.
- **Pin the version in Terraform (`live_version` variable).** Durable, but it
  needs a commit and a pipeline run: no faster than a revert.
- **Integrate API Gateway with `$LATEST`.** Changes go live as soon as code is
  uploaded, and there is nothing to move back to.
- **Weighted alias or CodeDeploy canary.** Gradual traffic shifting is more
  machinery than one health route needs today; it can be added on top of the
  same alias later.
