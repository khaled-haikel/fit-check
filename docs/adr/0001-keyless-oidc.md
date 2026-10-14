# 0001. Keyless deploys with GitHub OIDC, bootstrapped from AWS CloudShell

- Status: Accepted
- Date: 2026-10-13 (updated 2026-10-14 for stage 2: evaluation role, stage 3 services)

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
- Three roles, separated by trust and purpose:
  - `fitcheck-ci-plan` trusts only `pull_request` tokens from this repository.
    It is read-only; plans run with `-lock=false` ([ADR 0002](0002-s3-state-native-lock.md)).
  - `fitcheck-ci-apply` trusts only `ref:refs/heads/main`. It deploys
    `infra/app` and runs the alias rollback. Its jobs must not declare a
    GitHub environment, which would change the `sub` claim.
  - `fitcheck-ci-eval` trusts `pull_request` and `ref:refs/heads/main`. It can
    only invoke Titan Text Embeddings V2 and Claude Sonnet 5.5 on Bedrock: no
    state, no infrastructure. Pull requests use it for the retrieval
    evaluation; the deploy job uses it to build the vector index, then assumes
    the apply role for Terraform, so the apply role stays infrastructure-only.
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

- `fitcheck-*` prefixes on Lambda functions, log groups, IAM roles, DynamoDB
  tables (and `/index/*` under them), SQS queues, SNS topics (subscription ARNs
  extend the topic ARN) and CloudWatch alarms: we choose those names, so the
  prefix is the scope.
- `<state bucket>/app/*`: the state objects and lock files of `infra/app`.
- Lambda event source mappings: their ARNs end in a UUID, and
  `CreateEventSourceMapping` has no resource type, so it takes `"*"`. Create,
  get, update and delete are conditioned on `lambda:FunctionArn` matching a
  `fitcheck-*` function. The tag actions accept no such condition and stay on
  `event-source-mapping:*`; they change tags only.
- API Gateway `/apis/*`: HTTP API ARNs carry an AWS-generated ID, not a
  name. These statements are conditioned on `apigateway:Resource/ApiName`
  (`fitcheck-*`) and `aws:ResourceTag/project` (`fit-check`) where API Gateway
  supplies the keys, with `IfExists` because child resources such as routes do
  not carry them.
- API Gateway `/tags/*`: tag paths embed the target's ARN, which has no API
  name, so this statement cannot be scoped to fit-check's APIs. When this was
  written, the account had no other API Gateway API.
- `"*"` for `logs:DescribeLogGroups` and for the two X-Ray write actions in the
  boundary: these actions have no resource-level permissions.

API Gateway's tag-on-create does not match the Service Authorization
Reference, and the first two deploys failed on it. Creating an API with tags
is authorized as `apigateway:POST` on `/tags/...`, although the reference
lists only `PUT` for tags. Creating a stage with tags requires
`apigateway:TagResource` on `/apis/{id}/stages`, an action the reference does
not list at all; it is granted in the `/apis/*` statement, under the same
conditions. The stage 3 permissions (DynamoDB, SQS, SNS, alarms, event source
mappings) were derived the same way, from the provider's calls and the
reference, and are first exercised by the stage 3 deploy; tag-on-create may
still need a bootstrap re-apply there.

Bedrock needs no wildcard: Titan V2 is one foundation-model ARN, and Claude
Sonnet 5.5 is the `us.` inference profile ARN plus the foundation model in
each of the profile's destination regions, the latter only when the request
comes through that profile (`bedrock:InferenceProfileArn`). See
[ADR 0004](0004-bedrock-invocation-path.md).

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
