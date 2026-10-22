# 0008. A static page on GitHub Pages

- Status: Accepted
- Date: 2026-10-21

## Context

The analysis API (ADR 0006) answers `POST /checks` and `GET /checks/{id}`.
A recruiter needs a page instead: paste a job posting, follow the analysis,
and read each requirement with its verdict and the evidence it cites. The
audience is international, so the page is in English.

The project rules out anything billed per idle hour, and this stage adds no
AWS infrastructure. Public analyses are capped by the limits of ADR 0006: 5
new analyses per client IP per day and 2 USD of public budget per month.

## Decision

### GitHub Pages, built from this repository

- **Hosting:** the page is a static site on the GitHub Pages of this
  repository, at <https://khaled-haikel.github.io/fit-check/>, with GitHub
  Actions as its source.
- **Publication:** `.github/workflows/pages.yml` builds it on pushes to main
  that touch `page/` or `evals/results/`, and by hand.
  - It publishes with the official actions, `upload-pages-artifact` and
    `deploy-pages`, pinned by commit SHA.
  - Only its deploy job holds `pages: write` and `id-token: write`; the job
    that installs packages and builds holds only `contents: read`.
  - It ends with a smoke test: the page answers 200 with its title and this
    build's script, and the script answers 200.
- **Stack:** Vite, React, TypeScript and Tailwind, one page without a router,
  in `page/` with its own `package.json`.
  - Dependencies are pinned to exact versions, with the lockfile committed.
  - The build script lints and checks the types before it bundles.
  - vitest covers the error messages, the end of polling (done, failed and
    the five-minute limit), the order of the requirements and the rendering
    of a recorded example.
- **Pull requests:** `.github/workflows/page-ci.yml` runs the same tests and
  build on pull requests that change `page/`. It is a workflow of its own
  because the path filters of `pull-request.yml` start the paid evaluations.

The page polls `GET /checks/{id}` at the interval `POST` returns, for at most
five minutes, and stops when it is left. The requirements appear as soon as
they are extracted, before any verdict.

### One origin for CORS

The API allows one browser origin, `ALLOWED_ORIGIN`, set to
`https://khaled-haikel.github.io`. An origin has no path, so `/fit-check/`
does not enter it. The preflight allows `GET` and `POST` with a `content-type`
header, and error answers carry the same header, so the page can read their
`error` code. Any other origin gets no CORS header.

The page sends a content security policy of its own:

- scripts, styles and images come only from the page itself;
- network calls go only to the API's origin, which the build reads from
  `VITE_API_URL`.

### Recorded examples, so a visitor spends nothing

`page/src/examples/` holds the real `GET /checks/{id}` answers for the job
descriptions of `dev-01` and `dev-02`. They were run once through the public
API, and each one shows its date and `analysis_version`. Opening them calls
nothing, so a visitor can read a full result without using the daily limit or
the public budget. The page sends visitors there when the limit or the budget
refuses an analysis.

`page/scripts/refresh-examples.mjs` records them again. Each run is a paid
analysis unless the API already has that job description under the current
`analysis_version`.

### Numbers from the results file

The test-split numbers on the page are read at build time from the newest
`evals/results/analysis-test-<date>.json`: judgment accuracy, overestimation
and severe overestimation. Only that file's `test_runs` summary enters the
bundle, and no number is written by hand.

### Nothing that tracks

The page has no analytics, cookies, third-party scripts or web fonts; it uses
the system's fonts.

## Alternatives considered

- **S3 with CloudFront.** It would add a bucket, a distribution and their
  permissions to the bootstrap and the CI roles, all for a few static files.
  GitHub Pages costs nothing and needs no new infrastructure.
- **AWS Amplify Hosting.** It is another AWS service with its own build
  pipeline, billed beyond its free tier, and the page would be deployed
  outside this repository's workflows.
- **Serving the page from the API.** Every page view would invoke the
  Lambda, and a page change would need an API deploy.

## Consequences

- The page and the API are on different origins, so the API keeps its CORS
  setting. A copy of the page on another origin, including a local one,
  cannot start analyses; it can still show the examples.
- The API URL is in the bundle. It was public already; the limits of ADR 0006
  protect the budget.
- The examples show the `analysis_version` they were recorded with. When a
  change makes a new one, they keep showing the old one until they are
  recorded again.
- A new test result in `evals/results/` republishes the page with its numbers.
