// How the page orders verdicts, requirement types and analysis steps, and formats numbers and dates.
// The names shown for them are in i18n.ts.
import type { ActiveStatus, Requirement, RequirementType, Verdict } from "../api/types";

export const VERDICTS: Verdict[] = ["meets", "partial", "does_not_meet"];

export const STEPS: ActiveStatus[] = ["queued", "extracting", "retrieving", "judging"];

export interface VerdictGroup {
  verdict: Verdict;
  requirements: Requirement[];
}

const TYPE_RANK: Record<RequirementType, number> = { must: 0, nice: 1 };

// Groups in the order meets, partial, does not meet; must before nice inside each, then in posting order.
export function groupByVerdict(requirements: Requirement[]): VerdictGroup[] {
  return VERDICTS.map((verdict) => ({
    verdict,
    requirements: requirements
      .filter((requirement) => requirement.verdict === verdict)
      .sort((a, b) => TYPE_RANK[a.type] - TYPE_RANK[b.type]),
  })).filter((group) => group.requirements.length > 0);
}

export function formatPercent(share: number, locale = "en-US"): string {
  return new Intl.NumberFormat(locale, { style: "percent", minimumFractionDigits: 1, maximumFractionDigits: 1 }).format(share);
}

export function formatNumber(value: number, locale = "en-US", fractionDigits = 0): string {
  return value.toLocaleString(locale, { minimumFractionDigits: fractionDigits, maximumFractionDigits: fractionDigits });
}

export function formatDate(iso: string, locale = "en-US"): string {
  return new Date(iso).toLocaleDateString(locale, { year: "numeric", month: "long", day: "numeric", timeZone: "UTC" });
}
