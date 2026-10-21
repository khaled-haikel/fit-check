import { formatDate, formatPercent } from "../lib/results";
import { TEST_RESULTS } from "../lib/testResults";

const REPO = "https://github.com/khaled-haikel/fit-check";

export function HowItWorks() {
  return (
    <section id="how-it-works" aria-labelledby="how-heading">
      <h2 id="how-heading" className="font-serif text-3xl">
        How it works
      </h2>
      <ol className="mt-4 list-decimal space-y-3 pl-6 leading-relaxed">
        <li>
          Claude Sonnet 4.6 on Amazon Bedrock extracts the requirements of the job description. Each one keeps the excerpt
          it came from.
        </li>
        <li>A hybrid search, by keywords and by meaning, finds evidence cards for each requirement.</li>
        <li>Claude judges each requirement against those cards: meets, partially meets or does not meet.</li>
        <li>
          Code checks every citation: the card must be among those retrieved for that requirement and the quote must appear
          in it verbatim. A verdict without a valid citation becomes does not meet.
        </li>
        <li>When a reply from the model cannot be validated, the analysis fails instead of guessing.</li>
      </ol>

      {TEST_RESULTS && (
        <div className="mt-6 rounded-lg border border-rule bg-surface p-5">
          <h3 className="font-serif text-xl">How well it judges</h3>
          <p className="mt-2 text-muted">
            Measured once on a held-out test split of {TEST_RESULTS.n} labeled requirements, on{" "}
            {formatDate(`${TEST_RESULTS.date}T00:00:00Z`)}.
          </p>
          <dl className="mt-4 grid gap-4 sm:grid-cols-3">
            <div>
              <dt className="text-sm text-muted">Judgment accuracy</dt>
              <dd className="font-serif text-3xl">{formatPercent(TEST_RESULTS.accuracy)}</dd>
            </div>
            <div>
              <dt className="text-sm text-muted">Overestimation: a verdict above the label</dt>
              <dd className="font-serif text-3xl">{formatPercent(TEST_RESULTS.overestimation)}</dd>
            </div>
            <div>
              <dt className="text-sm text-muted">Severe overestimation: meets where the label is does not meet</dt>
              <dd className="font-serif text-3xl">{formatPercent(TEST_RESULTS.severeOverestimation)}</dd>
            </div>
          </dl>
        </div>
      )}

      <ul className="mt-6 flex flex-wrap gap-x-6 gap-y-2">
        <li>
          <a href={REPO} className="text-accent underline underline-offset-2">
            Source code
          </a>
        </li>
        <li>
          <a href={`${REPO}/tree/main/docs/adr`} className="text-accent underline underline-offset-2">
            Architecture decision records
          </a>
        </li>
      </ul>
    </section>
  );
}
