import type { Check, VerdictCounts } from "../api/types";
import { useLang } from "../i18n";
import { formatDate, formatNumber, groupByVerdict } from "../lib/results";
import { RequirementCard } from "./RequirementCard";
import { VerdictIcon } from "./VerdictBadge";

function Counts({ label, counts }: { label: string; counts: VerdictCounts }) {
  const { t } = useLang();
  const total = counts.meets + counts.partial + counts.does_not_meet;
  return (
    <div>
      <dt className="text-sm font-semibold uppercase tracking-wide text-muted">{label}</dt>
      <dd className="mt-1">
        {total === 0 ? t.result.none : t.result.counts(total, counts.meets, counts.partial, counts.does_not_meet)}
      </dd>
    </div>
  );
}

export function Result({ check, headingId }: { check: Check; headingId?: string }) {
  const { t } = useLang();
  const groups = groupByVerdict(check.requirements);
  const score = check.score;
  return (
    <div>
      {score && (
        <section aria-labelledby={headingId} className="rounded-lg border border-rule bg-surface p-5 sm:flex sm:items-center sm:gap-8">
          <div>
            <h3 id={headingId} className="text-sm font-semibold uppercase tracking-wide text-muted">
              {t.result.score}
            </h3>
            <p className="font-serif text-5xl leading-none">
              {score.value}
              <span className="text-2xl text-muted"> / 100</span>
            </p>
          </div>
          <dl className="mt-4 grid gap-3 sm:mt-0">
            <Counts label={t.types.must} counts={score.must} />
            <Counts label={t.types.nice} counts={score.nice} />
          </dl>
        </section>
      )}

      {groups.map((group) => (
        <section key={group.verdict} className="mt-8">
          <h3 className="flex items-center gap-2 font-serif text-2xl">
            <VerdictIcon verdict={group.verdict} className="h-5 w-5" />
            {t.verdicts[group.verdict]} ({group.requirements.length})
          </h3>
          <div className="mt-3 space-y-4">
            {group.requirements.map((requirement) => (
              <RequirementCard key={requirement.id} requirement={requirement} />
            ))}
          </div>
        </section>
      ))}

      <footer className="mt-8 border-t border-rule pt-4 text-sm text-muted">
        <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1">
          <dt>{t.result.analyzed}</dt>
          <dd>{formatDate(check.created_at, t.locale)}</dd>
          <dt>{t.result.cost}</dt>
          <dd>{formatNumber(check.usage.cost_usd, t.locale, 3)} USD</dd>
          {check.usage.latency_ms !== null && (
            <>
              <dt>{t.result.latency}</dt>
              <dd>{formatNumber(check.usage.latency_ms / 1000, t.locale, 1)} s</dd>
            </>
          )}
          <dt>{t.result.model}</dt>
          <dd className="break-all">{check.model}</dd>
          <dt>analysis_version</dt>
          <dd className="font-mono">{check.analysis_version.slice(0, 12)}</dd>
        </dl>
      </footer>
    </div>
  );
}
