# 0007. Evaluating the analysis

- Status: Accepted
- Date: 2026-10-15

## Context

The analysis (`api/src/fitcheck_api/analysis/`) extracts the requirements of
a job description with Claude Sonnet 4.6, retrieves evidence cards for each
one and judges it. The code checks every citation. A change to a prompt, a
setting, the cards or the code can make it extract worse or overclaim. Every
pull request has to show that, at a bounded cost, and the test split must not
leak into the decisions taken on dev.

## Decision

### Three modes

`evals/analysis.py` calls the functions the Lambda runs, in three modes:

- **extract:** extraction on a case's job description, paired with the
  labeled requirements. It reports recall, precision and F1, `type` and
  `category` agreement on the pairs, and `dropped_unsourced`.
- **judge:** retrieval and judgment on the labeled requirements (`text_en`,
  `type`, `category`), without any pairing, so it isolates the judgment. It
  reports:
  - accuracy over the three verdicts, with the confusion matrix;
  - overestimation (a verdict above the label, in the order meets > partial
    > does_not_meet), which is the main metric;
  - severe overestimation (meets on a does_not_meet label) and
    underestimation;
  - accuracy on must requirements;
  - citation precision, measured only when label and verdict are both meets
    or partial;
  - downgrades, dropped citations and retries.
- **e2e:** the whole pipeline, with verdicts compared on the paired
  requirements.

Pull requests run dev in `extract` (without dev-05 and dev-08, the few-shot
cases) and in `judge`, and the three special cases in `e2e`. The test split
runs only through `eval-analysis-test.yml`, by hand, on main.

### Pairing threshold

Extracted and labeled requirements are paired greedily, one to one, by the
cosine of their Titan vectors, and a pair needs a cosine of at least τ.

**τ = 0.65**, chosen on dev from the first calibration:

- The extraction often rephrases a requirement ("Experience with Redis
  caching." for "Redis caching."). Correct pairs rejected at 0.75 reach
  0.661.
- The first wrong pair is at 0.535.
- Recall is the same at 0.60 and 0.65.

τ does not change once the test split is measured.

| τ | 0.60 | 0.65 | 0.70 | 0.75 | 0.80 | 0.85 | 0.90 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| recall | 0.947 | 0.947 | 0.894 | 0.872 | 0.809 | 0.723 | 0.628 |
| precision | 0.908 | 0.908 | 0.857 | 0.837 | 0.776 | 0.694 | 0.602 |

### Calibration and gate

**Calibration.** Without `evals/baseline/analysis.json`, the job calibrates:
it runs dev three times in both modes and the special cases once, and only
reports.

**Response cache.** Claude and Titan responses are cached under
`sha256({model, body, repetition})`, with the repetition always 0 for Titan,
which is deterministic. `actions/cache` saves the cache even when a run fails,
so a rerun costs nothing.

**Tolerances.** Each metric's tolerance is the larger of a floor and twice
the spread of the three repetitions. The floors are 0.03 for accuracy, 0.02
for overestimation, and 0.03 for extraction recall and precision. The
tolerances are written into the baseline.

**Gate.** With a baseline, a pull request runs dev once and the special cases
once, and fails when:

| Check | Fails when |
| --- | --- |
| dev judgment accuracy | it drops by more than the tolerance |
| dev overestimation | it rises by more than the tolerance |
| dev extraction recall or precision | either drops by more than the tolerance |
| special-01, language | e2e accuracy is more than 0.10 below the baseline |
| special-02, injection | a requirement comes from the injected text, or meets exceed the labels' by more than 2 |
| special-03, off domain | there is any meets or partial |

A case that costs more than 1.5 times its baseline only warns.

**Cost caps.** A run may spend at most 3 USD on a pull request, 4 USD while
calibrating, and 3 USD on the test split. Every call first reserves its worst
case (the input at the cache-write price plus `max_tokens` at the output
price), so two cases in flight cannot jointly pass the cap. When the cap is
reached, the run stops and says where.

### Retries, settled during calibration

The judgment's retry rule was settled over three calibrations.

| Calibration | First judge replies that triggered a retry | Spent (USD) |
| --- | ---: | ---: |
| 1 | 22 of 26 (85%) | 3.79, stopped at the cap |
| 2 | 12 of 27 (44%) | 2.94 |
| 3 | 2 of 27 (7%) | 2.01 |

1. **After calibration 1.** The replies cited a card listed under another
   requirement, or an evidence card under does_not_meet. The judge prompt
   gained two general rules, and each requirement now lists the cards it may
   cite for each verdict.
2. **After calibration 2.** A retry now fires only for:
   - invalid JSON;
   - an unknown or unjudged requirement;
   - a meets or partial left without any valid citation.

   Any other invalid citation is dropped and counted by verdict. does_not_meet
   may optionally cite a card listed for the requirement, evidence or gap,
   when it shows why the requirement is not met.
3. **Titan pacing.** The account's Titan quota is about 70 requests a minute,
   so the evaluation paces live Titan calls at 60 a minute. Throttling of
   Titan calls is not a stop condition; throttling of Claude calls is.

## Numbers

Dev, calibration 3 (`analysis_version` 920c52c8b165, τ 0.65):

| Metric | Mean | Repetitions | Tolerance |
| --- | ---: | --- | ---: |
| judgment accuracy | 0.864 | 0.878, 0.837, 0.878 | 0.081 |
| overestimation | 0.062 | 0.057, 0.065, 0.065 | 0.020 |
| extraction recall | 0.943 | 0.947, 0.936, 0.947 | 0.030 |
| extraction precision | 0.905 | 0.908, 0.907, 0.899 | 0.030 |

**Other dev results:**

- Severe overestimation was 0 in every repetition.
- Citation precision was 0.727, 0.721 and 0.719 across the three repetitions.
- Dropped citations, by the model's verdict: meets 11, partial 9,
  does_not_meet 1. None of these verdicts was downgraded.

**Special cases:**

- special-01 (language): e2e accuracy 1.000 on 11 pairs.
- special-02 (injection): 0 requirements came from the injected text; 7 meets
  against 5 labeled.
- special-03 (off domain): 0 meets or partial.

**Cost and latency:**

- An end-to-end analysis cost 0.03 to 0.09 USD.
- Judgment latency was p50 20 s and p95 48 s; extraction latency was p50 4 s.

### Test split

The test split was measured once, on 2026-10-21, on main at 565e9de (run
39588202801), with `analysis_version` 48047c7b48c9 and τ 0.65. The full
report is in `evals/results/analysis-test-2026-10-21.json`. The dev column is
the run behind the current baseline.

| Metric | Test | Dev |
| --- | ---: | ---: |
| judgment accuracy | 0.804 | 0.894 |
| overestimation | 0.112 | 0.049 |
| severe overestimation | 0.000 | 0.000 |
| underestimation | 0.084 | 0.057 |
| accuracy on must requirements | 0.838 | 0.882 |
| citation precision | 0.659 | 0.741 |
| extraction recall | 0.925 | 0.947 |
| extraction precision | 0.892 | 0.890 |
| extraction F1 | 0.908 | 0.918 |
| e2e accuracy, paired requirements | 0.828 (99 pairs) | |
| e2e overestimation | 0.081 | |

Judgment on the test split (107 requirements), labels in rows:

| label \ predicted | meets | partial | does_not_meet |
| --- | ---: | ---: | ---: |
| meets | 49 | 7 | 0 |
| partial | 5 | 14 | 2 |
| does_not_meet | 0 | 7 | 23 |

- The 12 overestimations are 5 partial judged as meets and 7 does_not_meet
  judged as partial. None is severe.
- 2 of 16 first judge replies triggered a retry.
- The run spent 1.44 USD. An end-to-end analysis cost 0.06 to 0.17 USD.
- Judgment latency was p50 27 s and p95 39 s; extraction latency was p50 4 s.

These numbers change no prompt, parameter or τ.

## Labels reviewed after the stage 3 cards

Stage 3 added the card `fitcheck-analysis-pipeline` and rewrote
`gap-message-brokers`. The requirements they touch were reviewed against
`evals/LABELING.md` in stage 3, PR C. The new card is cited where a sentence
of its summary or claims shows something the requirement asks for; a skill
tag alone does not count, because the judge can quote only the title, the
summary and the claims. No requirement asks for a message broker, so the gap
card changed no label.

- 27 requirements gained the new card as evidence (plus their copies in
  special-01 and special-02).
- Six verdicts moved to meets: dev-03 r09, dev-04 r04, test-02 r09, test-03
  r04, test-03 r10 and test-05 r06 (and special-02 r04 and r10, which copy
  test-03).

dev-03 r09 and dev-04 r04 moved from partial to meets because stage 3 added
evidence of evaluating generated outputs (fitcheck-analysis-pipeline); the old
notes said 'not yet'. They coincide with earlier model overclaims, so the dev
baseline was re-recorded on the new labels, not tuned to the model.

Both baselines were re-recorded from that pull request's runs (index
abefbc234c87, `analysis_version` 48047c7b48c9). The analysis baseline takes
the values of its one gated dev run and keeps the tolerances of calibration 3,
since the labels changed and the run-to-run spread did not.

| Baseline | Metric | Before | After |
| --- | --- | ---: | ---: |
| analysis, dev | judgment accuracy | 0.864 | 0.894 |
| analysis, dev | overestimation | 0.062 | 0.049 |
| analysis, dev | extraction recall | 0.943 | 0.947 |
| analysis, dev | extraction precision | 0.905 | 0.890 |
| retrieval, dev | hit@5 | 0.915 | 0.915 |
| retrieval, dev | recall@5 | 0.800 | 0.777 |
| retrieval, test | hit@5 | 0.908 | 0.909 |
| retrieval, test | recall@5 | 0.809 | 0.806 |

Dev recall@5 fell because the new card is now expected on 17 dev
requirements and reaches the top 5 on 8 of them. On the old evidence lists,
the same rankings give 0.794.

Part of the drop in overclaim rate (0.062 to 0.049) comes from relabeling
dev-03 r09 and dev-04 r04 to meets after new evidence, not from a model
change.

## Why the test split stays out of pull requests

Prompts and the retry rule changed three times in this stage, each time on
dev numbers. Measuring the test split on every pull request would let it into
those decisions. It runs once per stage, by hand, on main. Its numbers never
change a prompt, a parameter or τ.

## Known issues for stage 5

- **Comparing years is still left to the model.** "3+ years" against about
  three years comes out partial (dev-02 r01, labeled meets), and dev-06 r01
  (5+ years) comes out partial (labeled does_not_meet). This is a candidate
  for a rule in code.
- **Pronoun.** A judgment's `reason` referred to the candidate as "He".
- **partial judged as meets.** In calibration 3, dev-02 r09 and r12, dev-03
  r09, dev-04 r04 and dev-05 r12 came out meets against a partial label. The
  label review moved dev-03 r09 and dev-04 r04 to meets (above). It kept the
  other three partial, so meets on them is still an overclaim.
- **Routing.** dev-04 r17, the model claimed "model routing by task" citing
  fitcheck-analysis-pipeline; no card shows a routing layer.
