# 0004. Claude Sonnet 4.6 on Bedrock InvokeModel, with Sonnet 5.x as upgrades

- Status: Accepted
- Date: 2026-10-14

## Context

Stage 3 generates the analysis with a Claude model on Amazon Bedrock; stage 2
only embeds, with Titan Text Embeddings V2. The plan named Claude Sonnet 5.5,
reached either through InvokeModel on `bedrock-runtime` with the `us.`
inference profile (path A) or through the `bedrock-mantle` Messages endpoint
(path B). `scripts/bedrock_probe.py`, run three times from CloudShell with the
administrator's credentials (read-only, at most 12 model calls), decided the
question.

## What the probe found

- **Claude Sonnet 5.5 and Sonnet 5 are blocked for this account.** InvokeModel
  returned `403 AccessDeniedException: anthropic.claude-sonnet-5-5 is not
  available for this account`; `bedrock-mantle` returned `404 The model
  'anthropic.claude-sonnet-5-5' does not exist`. Sonnet 5 got the same 403.
  `get-foundation-model-availability` for `anthropic.claude-sonnet-5` showed
  `authorizationStatus` AUTHORIZED and `entitlementAvailability` and
  `regionAvailability` AVAILABLE, but `agreementAvailability` stayed
  NOT_AVAILABLE because no call ever got through. AWS documents this case:
  *"Some models require additional account-level access. Completing model
  activation requirements, such as AWS Marketplace subscription and provider
  prerequisites, doesn't grant this additional access. … contact AWS Sales for
  additional access options."*
- **Claude Sonnet 4.6 works on path A.** The first InvokeModel call created the
  AWS Marketplace subscription automatically (*"When you invoke a third-party
  model for the first time in your account, Amazon Bedrock automatically
  initiates the subscription process in the background"*), and the agreement
  is now AVAILABLE. Sonnet 4.6 accepted:
  - adaptive thinking with `output_config.effort`;
  - structured output with `output_config.format` (`json_schema`);
  - `tool_choice` auto and forced;
  - `temperature: 0` without thinking;
  - explicit `cache_control`, created then read on a ~1,300-token prefix (the
    minimum is 1,024 tokens).

  Path B does not exist for Sonnet 4.6: its AWS model card lists
  `bedrock-runtime` only.
- Titan V2 returns 1,024 normalized dimensions on demand in `us-east-1`.
- From `us-east-1`, the `us.` profiles of Sonnet 4.6, 5 and 5.5 route to
  `us-east-1`, `us-east-2` and `us-west-2` (probe and AWS model cards). AWS
  states that a geography-bound profile's destination list never changes.

## Decision

- **Generation model: Claude Sonnet 4.6** (`us.anthropic.claude-sonnet-4-6`,
  base model `anthropic.claude-sonnet-4-6`), called with InvokeModel on
  `bedrock-runtime`. The fallback, Sonnet 4.5, was not needed. It would have
  been a poor long-term choice anyway: it has been Legacy on Bedrock since
  2026-10-08, with EOL on 2027-04-08.
- **Model IDs are configuration, never code.** In the bootstrap they are
  `embedding_model_id` and `generation_models`; the Lambda environment and the
  evaluation configuration carry the active ID.
- **The regional `us.` profile, not `global.`.** Regional endpoints cost 10 %
  more than global ones. We accept that because requests stay in three US
  regions, and because IAM can then list the exact destination ARNs; the
  global profile routes to every commercial region, which would need a region
  wildcard. At this project's volume the premium is cents a month.
- **IAM also grants Sonnet 5 and 5.5**, with the same three destinations.
  These grants open nothing until AWS grants access, and they save a
  CloudShell re-apply when it does (ADR 0001).

## Retirement

On Bedrock, Sonnet 4.6 is Active. Its model card gives: launched
2026-02-17, *EOL no sooner than* 2027-02-17, a Legacy period of at least six
months, and no EOL date announced. Because it was not Legacy on 2026-10-14,
the earliest possible EOL is mid-April 2027. Anthropic's own date (not before
2027-02-17) does not apply to Bedrock; Bedrock sets its own.

## Upgrade path

1. Wait for AWS to grant access. Confirm with
   `aws bedrock get-foundation-model-availability --model-id anthropic.claude-sonnet-5-5`,
   whose `agreementAvailability` must be AVAILABLE after a first call.
2. Re-run the probe with the new model as `PRIMARY`.
3. In a pull request, change only the configured model ID. IAM already covers
   Sonnet 5 and 5.5.
4. Run the evaluation and compare it with the Sonnet 4.6 baseline. Merge only
   if it is not worse; the alias rollback (ADR 0003) reverts the model, because
   the ID is part of each version's configuration.

So that step 3 stays a one-line change, stage 3 should not depend on what
Sonnet 4.6 accepts but the upgrades reject on Bedrock:

- structured outputs (not supported for Sonnet 5 and 5.5 on Bedrock): validate
  the JSON in code instead;
- forced `tool_choice` (400 on Sonnet 5.5);
- non-default `temperature`, `top_p` or `top_k` (400 on Sonnet 5 and 5.5).

## Alternatives considered

- **Path B (`bedrock-mantle`)**: not offered for Sonnet 4.6. For 5.x, its IAM
  action `bedrock-mantle:CreateInference` is scoped to a Bedrock project rather
  than to model ARNs, a resource outside the `fitcheck-` prefix.
- **Global inference profile**: 10 % cheaper, but it routes worldwide and
  needs a region wildcard in IAM.
- **Waiting for Sonnet 5.5**: it would block stage 3 on an AWS sales process
  with no date.
