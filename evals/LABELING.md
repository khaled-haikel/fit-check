# Golden set: how the cases are labeled

The golden set measures two things: whether retrieval finds the right evidence for a
requirement, and (from stage 3) whether the full pipeline extracts the right requirements
and reaches the right verdict for each one.

## Sources

- `real-condensed`: a real job posting, condensed and anonymized. Company names, product
  names and anything that identifies the employer are removed. Benefits and compensation
  are kept short.
- `synthetic`: written for the eval. Today only the off-domain control case.
- `derived`: a controlled variant of another case (`derived_from`). It keeps the source's
  labels, so any difference in the output is caused by the variation alone.

## Splits

- `dev`: used to iterate on prompts. Few-shot examples may only come from here.
- `test`: used only to report final numbers. Never tune against it.
- `special`: invariance checks.
  - `language`: the same posting in Portuguese. Verdicts must match the English source.
  - `injection`: the same posting with an instruction aimed at AI screeners. Verdicts must
    match the clean source.
  - `off_domain`: a non-software role. Every requirement must be `does_not_meet`.

## Which requirements are labeled

1. Take requirements from the qualification sections (required, preferred, nice to have).
   Use the responsibilities only when a posting has no qualification list.
2. Split a bullet that joins distinct skills with "and" when they can be judged
   separately ("Docker, Kubernetes and CI/CD" becomes three requirements). Keep "or" lists
   and "such as" lists as one requirement.
3. Keep years of experience, degrees, language and location as requirements.
4. Leave out equipment, benefits, compensation and application steps.

`type` is `must` for required qualifications and `nice` for preferred or nice-to-have ones.

## Verdicts

- `meets`: the cited cards fully support the requirement, including any number of years.
- `partial`: the cards support part of it (a related tool, a smaller scale, fewer years in
  one language, a different degree).
- `does_not_meet`: no card supports it, or a hard constraint fails (location, years well
  below the ask, a degree level not held).

Years: about three years of professional experience. "3+ years" meets; "5+ years" does not.

## Evidence

- `meets` and `partial` must cite at least one card. Cite every card that genuinely
  supports the requirement, and only those.
- `does_not_meet` cites nothing, or only `gap` cards that state the gap.
- Labels are relative to the cards at labeling time. When a card is added or changed, the
  requirements it touches must be reviewed. Stage 3 adds Bedrock evidence; revisit the
  requirements whose note says so.
