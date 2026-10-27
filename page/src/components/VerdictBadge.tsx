import type { Verdict } from "../api/types";
import { useLang } from "../i18n";

const STYLE: Record<Verdict, string> = {
  meets: "bg-meets-bg text-meets border-meets",
  partial: "bg-partial-bg text-partial border-partial",
  does_not_meet: "bg-miss-bg text-miss border-miss",
};

export function VerdictIcon({ verdict, className = "h-4 w-4" }: { verdict: Verdict; className?: string }) {
  return (
    <svg viewBox="0 0 16 16" className={className} aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="2">
      {verdict === "meets" && <path d="M3 8.5l3.2 3L13 4.5" strokeLinecap="round" strokeLinejoin="round" />}
      {verdict === "partial" && (
        <>
          <circle cx="8" cy="8" r="5.5" />
          <path d="M8 2.5a5.5 5.5 0 0 1 0 11z" fill="currentColor" stroke="none" />
        </>
      )}
      {verdict === "does_not_meet" && <path d="M4 4l8 8M12 4l-8 8" strokeLinecap="round" />}
    </svg>
  );
}

// Icon and text together, so the verdict never depends on color alone.
export function VerdictBadge({ verdict }: { verdict: Verdict }) {
  const { t } = useLang();
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-sm font-semibold ${STYLE[verdict]}`}>
      <VerdictIcon verdict={verdict} />
      {t.verdicts[verdict]}
    </span>
  );
}
