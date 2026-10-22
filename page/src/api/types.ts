// The API contract, as GET /checks/{id} and POST /checks return it (api/src/fitcheck_api/checks/api.py).

export type Verdict = "meets" | "partial" | "does_not_meet";
export type ActiveStatus = "queued" | "extracting" | "retrieving" | "judging";
export type CheckStatus = ActiveStatus | "done" | "failed";
export type RequirementType = "must" | "nice";

export interface Proof {
  type: "repo" | "live" | "demo" | "doc" | "none";
  url?: string | null;
  note?: string | null;
}

export interface Citation {
  card_id: string;
  title: string;
  quote: string;
  proof: Proof[];
}

export interface Requirement {
  id: string;
  source: string;
  text_en: string;
  type: RequirementType;
  category: string;
  // null until the requirement is judged.
  verdict: Verdict | null;
  reason: string | null;
  downgraded: boolean | null;
  citations: Citation[];
}

export type VerdictCounts = Record<Verdict, number>;

export interface Score {
  value: number;
  must: VerdictCounts;
  nice: VerdictCounts;
}

export interface Check {
  check_id: string;
  status: CheckStatus;
  created_at: string;
  updated_at: string;
  analysis_version: string;
  model: string;
  index_version: string;
  requirements: Requirement[];
  // Only when status is done.
  score: Score | null;
  usage: { cost_usd: number; latency_ms: number | null };
  error: string | null;
}

export interface Started {
  checkId: string;
  pollAfterMs: number;
}
