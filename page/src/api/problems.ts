// Everything that can stop an analysis, and what the page offers for it. The texts are in i18n.ts.
import { MESSAGES, type Messages } from "../i18n";

export type ProblemKind =
  | "daily_limit"
  | "monthly_budget"
  | "busy"
  | "rate_limited"
  | "invalid"
  | "unavailable"
  | "not_found"
  | "failed"
  | "timed_out"
  | "poll_timeout"
  | "network"
  | "not_configured";

export interface Problem {
  kind: ProblemKind;
  // The error code of a failed analysis, such as bedrock_error.
  code?: string;
}

export class ApiProblem extends Error {
  readonly problem: Problem;

  constructor(problem: Problem) {
    super(problem.code ? `${problem.kind}: ${problem.code}` : problem.kind);
    this.name = "ApiProblem";
    this.problem = problem;
  }
}

export interface ProblemMessage {
  title: string;
  body: string;
  // Whether a "Try again" button makes sense.
  retry: boolean;
  // Whether to point to the recorded examples instead.
  examples: boolean;
  code?: string;
}

const OFFERS: Record<ProblemKind, { retry: boolean; examples: boolean }> = {
  daily_limit: { retry: false, examples: true },
  monthly_budget: { retry: false, examples: true },
  busy: { retry: true, examples: true },
  rate_limited: { retry: true, examples: false },
  invalid: { retry: true, examples: false },
  unavailable: { retry: true, examples: true },
  not_found: { retry: true, examples: false },
  failed: { retry: true, examples: false },
  timed_out: { retry: true, examples: false },
  poll_timeout: { retry: true, examples: false },
  network: { retry: true, examples: false },
  not_configured: { retry: false, examples: true },
};

export function describeProblem(problem: Problem, messages: Messages = MESSAGES.en): ProblemMessage {
  return {
    ...messages.problems[problem.kind],
    ...OFFERS[problem.kind],
    code: problem.kind === "failed" ? problem.code : undefined,
  };
}

export function problemFrom(error: unknown): Problem {
  return error instanceof ApiProblem ? error.problem : { kind: "network" };
}

export function isAbort(error: unknown): boolean {
  return typeof error === "object" && error !== null && (error as { name?: unknown }).name === "AbortError";
}
