import { useLang } from "../i18n";
import { formatDate, formatPercent } from "../lib/results";
import { TEST_RESULTS } from "../lib/testResults";

const REPO = "https://github.com/khaled-haikel/fit-check";

export function HowItWorks() {
  const { t } = useLang();
  return (
    <section id="how-it-works" aria-labelledby="how-heading">
      <h2 id="how-heading" className="font-serif text-3xl">
        {t.how.heading}
      </h2>
      <ol className="mt-4 list-decimal space-y-3 pl-6 leading-relaxed">
        {t.how.steps.map((step) => (
          <li key={step}>{step}</li>
        ))}
      </ol>

      {TEST_RESULTS && (
        <div className="mt-6 rounded-lg border border-rule bg-surface p-5">
          <h3 className="font-serif text-xl">{t.how.quality}</h3>
          <p className="mt-2 text-muted">{t.how.measured(TEST_RESULTS.n, formatDate(`${TEST_RESULTS.date}T00:00:00Z`, t.locale))}</p>
          <dl className="mt-4 grid gap-4 sm:grid-cols-3">
            <div>
              <dt className="text-sm text-muted">{t.how.accuracy}</dt>
              <dd className="font-serif text-3xl">{formatPercent(TEST_RESULTS.accuracy, t.locale)}</dd>
            </div>
            <div>
              <dt className="text-sm text-muted">{t.how.overestimation}</dt>
              <dd className="font-serif text-3xl">{formatPercent(TEST_RESULTS.overestimation, t.locale)}</dd>
            </div>
            <div>
              <dt className="text-sm text-muted">{t.how.severe}</dt>
              <dd className="font-serif text-3xl">{formatPercent(TEST_RESULTS.severeOverestimation, t.locale)}</dd>
            </div>
          </dl>
        </div>
      )}

      <ul className="mt-6 flex flex-wrap gap-x-6 gap-y-2">
        <li>
          <a href={REPO} className="text-accent underline underline-offset-2">
            {t.how.source}
          </a>
        </li>
        <li>
          <a href={`${REPO}/tree/main/docs/adr`} className="text-accent underline underline-offset-2">
            {t.how.adrs}
          </a>
        </li>
      </ul>
    </section>
  );
}
