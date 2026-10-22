// How the page orders and names verdicts, requirement types and analysis steps.
import type { ActiveStatus, Requirement, RequirementType, Verdict } from "../api/types";

export const VERDICTS: Verdict[] = ["meets", "partial", "does_not_meet"];

export const VERDICT_LABEL: Record<Verdict, string> = {
  meets: "Meets",
  partial: "Partially meets",
  does_not_meet: "Does not meet",
};

export const TYPE_LABEL: Record<RequirementType, string> = { must: "Must have", nice: "Nice to have" };

export const STEPS: { status: ActiveStatus; label: string }[] = [
  { status: "queued", label: "Queued" },
  { status: "extracting", label: "Extracting requirements" },
  { status: "retrieving", label: "Finding evidence" },
  { status: "judging", label: "Judging" },
];

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

export function formatPercent(share: number): string {
  return `${(share * 100).toFixed(1)}%`;
}

export function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString("en-US", { year: "numeric", month: "long", day: "numeric", timeZone: "UTC" });
}
