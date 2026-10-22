import type { Check, CheckStatus } from "../api/types";
import { STEPS, TYPE_LABEL } from "../lib/results";

export function Progress({ status, check }: { status: CheckStatus | "submitting"; check: Check | null }) {
  const current = STEPS.findIndex((step) => step.status === status);
  const label = status === "submitting" ? "Sending the job description" : (STEPS[current]?.label ?? "Working");
  const pending = check?.requirements ?? [];
  return (
    <section aria-labelledby="progress-heading" className="rounded-lg border border-rule bg-surface p-5">
      <h3 id="progress-heading" className="font-serif text-2xl">
        Analyzing
      </h3>
      <p className="sr-only" aria-live="polite">
        {label}
      </p>
      <ol className="mt-4 space-y-2">
        {STEPS.map((step, index) => {
          const done = current > index;
          const active = current === index;
          return (
            <li key={step.status} className={`flex items-center gap-3 ${active ? "font-semibold" : done ? "" : "text-muted"}`}>
              <span
                aria-hidden="true"
                className={`flex h-5 w-5 items-center justify-center rounded-full border text-xs ${
                  done ? "border-meets bg-meets-bg text-meets" : active ? "border-accent text-accent" : "border-rule"
                }`}
              >
                {done ? "✓" : active ? <span className="h-2 w-2 animate-pulse rounded-full bg-accent" /> : ""}
              </span>
              {step.label}
              <span className="sr-only">{done ? "(done)" : active ? "(in progress)" : "(waiting)"}</span>
            </li>
          );
        })}
      </ol>
      {pending.length > 0 && (
        <div className="mt-6">
          <h4 className="text-sm font-semibold uppercase tracking-wide text-muted">Requirements found ({pending.length})</h4>
          <ul className="mt-2 space-y-2">
            {pending.map((requirement) => (
              <li key={requirement.id} className="flex gap-3">
                <span className="shrink-0 text-sm text-muted">{TYPE_LABEL[requirement.type]}</span>
                <span>{requirement.text_en}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}
