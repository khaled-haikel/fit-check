# 0001. Keyless deploys with GitHub OIDC, bootstrapped from AWS CloudShell

- Status: Accepted
- Date: 2026-10-13

## Context

fit-check deploys to an AWS account that also hosts another project. The
repository is public, and so are its Actions logs. A long-lived AWS access key
in GitHub Secrets can leak through logs, a compromised action or a workflow
change, and it never expires on its own. The workstation has no AWS
credentials and should keep it that way. Still, something has to create the
trust anchor (OIDC provider, CI roles, state bucket) once, before CI can
authenticate at all.

## Decision

- GitHub Actions obtains AWS credentials only through OIDC: the job requests a
  token and STS exchanges it for one-hour role credentials
  (`AssumeRoleWithWebIdentity`). No access key exists for this project.
- Two roles, separated by trust:
  - `fitcheck-ci-plan` trusts only `pull_request` tokens from this repository.
    It is read-only; plans run with `-lock=false` ([ADR 0002](0002-s3-state-native-lock.md)).
  - `fitcheck-ci-apply` trusts only `ref:refs/heads/main`. It deploys
    `infra/app` and runs the alias rollback. Its jobs must not declare a
    GitHub environment, which would change the `sub` claim.
- Trust policies match the exact `sub` claim. This repository was created
  after 2026-07-15, so GitHub emits the immutable format
  `repo:khaled-haikel@72475193/fit-check@1417405917:...`; the numeric IDs keep a
  recreated or renamed repository with the same name from matching.
- Role ARNs live in GitHub repository variables, never in the workflow YAML.
- Escalation guard: the apply role can create or change only roles that carry
  the `fitcheck-workload-boundary` permissions boundary, can pass roles only to
  Lambda, and is explicitly denied any change to the `fitcheck-ci-*` roles and
  to the boundary policy. Without this, "IAM for `fitcheck-*` roles" would let
  CI grant itself administrator access.
- The administrator applies `infra/bootstrap` in AWS CloudShell, which runs
  with the console session's short-lived credentials. Changing CI permissions
  later means re-running it there.

## Wildcards

Every policy lists its actions explicitly, and `terraform test` in
`infra/bootstrap` fails if a statement has a wildcard in both Action and
Resource. Resource wildcards remain only where they cannot be avoided:

- `fitcheck-*` prefixes on Lambda functions, log groups and IAM roles: we
  choose those names, so the prefix is the scope.
- `<state bucket>/app/*`: the state objects and lock files of `infra/app`.
- API Gateway `/apis/*`: HTTP API ARNs carry an AWS-generated ID, not a
  name. These statements are conditioned on `apigateway:Resource/ApiName`
  (`fitcheck-*`) and `aws:ResourceTag/project` (`fit-check`) where API Gateway
  supplies the keys, with `IfExists` because child resources such as routes do
  not carry them.
- API Gateway `/tags/*`: tag paths embed the target's ARN, which has no API
  name, so this statement cannot be scoped to fit-check's APIs. When this was
  written, the account had no other API Gateway API. AWS authorizes creating an
  API or a stage with tags as `apigateway:POST` on `/tags/...`, although the
  Service Authorization Reference lists only `PUT` for tags; the first deploy
  failed on that.
- `"*"` for `logs:DescribeLogGroups` and for the two X-Ray write actions in the
  boundary: these actions have no resource-level permissions.

## Alternatives considered

- **IAM user with access keys in GitHub Secrets.** Rejected: a long-lived
  secret in a public repository's CI, rotated by hand.
- **Bootstrap from the workstation with a local access key.** Rejected: it
  creates exactly the kind of long-lived key this design removes, and leaves it
  on a laptop. CloudShell runs the same Terraform with credentials that expire
  with the console session.
- **IAM Identity Center or AWS Organizations.** Not used in this account.
- **One role for plan and apply.** Rejected: pull request code would run with
  write access.
