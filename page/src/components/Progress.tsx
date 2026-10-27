import type { Check, CheckStatus } from "../api/types";
import { useLang } from "../i18n";
import { STEPS } from "../lib/results";

export function Progress({ status, check }: { status: CheckStatus | "submitting"; check: Check | null }) {
  const { lang, t } = useLang();
  const current = STEPS.findIndex((step) => step === status);
  const label = status === "submitting" ? t.progress.sending : current >= 0 ? t.progress.steps[STEPS[current]] : t.progress.working;
  const pending = check?.requirements ?? [];
  return (
    <section aria-labelledby="progress-heading" className="rounded-lg border border-rule bg-surface p-5">
      <h3 id="progress-heading" className="font-serif text-2xl">
        {t.progress.heading}
      </h3>
      <p className="sr-only" aria-live="polite">
        {label}
      </p>
      <ol className="mt-4 space-y-2">
        {STEPS.map((step, index) => {
          const done = current > index;
          const active = current === index;
          return (
            <li key={step} className={`flex items-center gap-3 ${active ? "font-semibold" : done ? "" : "text-muted"}`}>
              <span
                aria-hidden="true"
                className={`flex h-5 w-5 items-center justify-center rounded-full border text-xs ${
                  done ? "border-meets bg-meets-bg text-meets" : active ? "border-accent text-accent" : "border-rule"
                }`}
              >
                {done ? "✓" : active ? <span className="h-2 w-2 animate-pulse rounded-full bg-accent" /> : ""}
              </span>
              {t.progress.steps[step]}
              <span className="sr-only">{done ? t.progress.done : active ? t.progress.active : t.progress.waiting}</span>
            </li>
          );
        })}
      </ol>
      {pending.length > 0 && (
        <div className="mt-6">
          <h4 className="text-sm font-semibold uppercase tracking-wide text-muted">{t.progress.found(pending.length)}</h4>
          <ul className="mt-2 space-y-2">
            {pending.map((requirement) => (
              <li key={requirement.id} className="flex gap-3">
                <span className="shrink-0 text-sm text-muted">{t.types[requirement.type]}</span>
                {lang === "pt" ? <span>{requirement.source}</span> : <span lang="en">{requirement.text_en}</span>}
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}
