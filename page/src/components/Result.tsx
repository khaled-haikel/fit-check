import type { Check, Verdict, VerdictCounts } from "../api/types";
import { VERDICT_LABEL, formatDate, groupByVerdict } from "../lib/results";
import { RequirementCard } from "./RequirementCard";
import { VerdictIcon } from "./VerdictBadge";

const COUNT_LABEL: Record<Verdict, string> = { meets: "meet", partial: "partial", does_not_meet: "do not meet" };

function Counts({ label, counts }: { label: string; counts: VerdictCounts }) {
  const total = counts.meets + counts.partial + counts.does_not_meet;
  return (
    <div>
      <dt className="text-sm font-semibold uppercase tracking-wide text-muted">{label}</dt>
      <dd className="mt-1">
        {total === 0
          ? "None listed"
          : `${total}: ${counts.meets} ${COUNT_LABEL.meets}, ${counts.partial} ${COUNT_LABEL.partial}, ${counts.does_not_meet} ${COUNT_LABEL.does_not_meet}`}
      </dd>
    </div>
  );
}

export function Result({ check, headingId }: { check: Check; headingId?: string }) {
  const groups = groupByVerdict(check.requirements);
  const score = check.score;
  return (
    <div>
      {score && (
        <section aria-labelledby={headingId} className="rounded-lg border border-rule bg-surface p-5 sm:flex sm:items-center sm:gap-8">
          <div>
            <h3 id={headingId} className="text-sm font-semibold uppercase tracking-wide text-muted">
              Fit score
            </h3>
            <p className="font-serif text-5xl leading-none">
              {score.value}
              <span className="text-2xl text-muted"> / 100</span>
            </p>
          </div>
          <dl className="mt-4 grid gap-3 sm:mt-0">
            <Counts label="Must have" counts={score.must} />
            <Counts label="Nice to have" counts={score.nice} />
          </dl>
        </section>
      )}

      {groups.map((group) => (
        <section key={group.verdict} className="mt-8">
          <h3 className="flex items-center gap-2 font-serif text-2xl">
            <VerdictIcon verdict={group.verdict} className="h-5 w-5" />
            {VERDICT_LABEL[group.verdict]} ({group.requirements.length})
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
          <dt>Analyzed</dt>
          <dd>{formatDate(check.created_at)}</dd>
          <dt>Cost</dt>
          <dd>{check.usage.cost_usd.toFixed(3)} USD</dd>
          {check.usage.latency_ms !== null && (
            <>
              <dt>Latency</dt>
              <dd>{(check.usage.latency_ms / 1000).toFixed(1)} s</dd>
            </>
          )}
          <dt>Model</dt>
          <dd className="break-all">{check.model}</dd>
          <dt>analysis_version</dt>
          <dd className="font-mono">{check.analysis_version.slice(0, 12)}</dd>
        </dl>
      </footer>
    </div>
  );
}
