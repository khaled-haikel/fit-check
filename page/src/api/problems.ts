// Everything that can stop an analysis, and the message the page shows for it.

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

export function describeProblem(problem: Problem): ProblemMessage {
  switch (problem.kind) {
    case "daily_limit":
      return {
        title: "Daily limit reached",
        body: "The daily limit of new analyses has been reached. Try again tomorrow, or open one of the examples below.",
        retry: false,
        examples: true,
      };
    case "monthly_budget":
      return {
        title: "Monthly budget used up",
        body: "This month's public budget for analyses has been used up. Open one of the examples below.",
        retry: false,
        examples: true,
      };
    case "busy":
      return {
        title: "Too many analyses running",
        body: "Too many analyses are in progress right now. Try again in a few minutes.",
        retry: true,
        examples: true,
      };
    case "rate_limited":
      return { title: "Too many requests", body: "Wait a moment and try again.", retry: true, examples: false };
    case "invalid":
      return {
        title: "The job description was not accepted",
        body: "It needs 200 to 20,000 characters. Check the text and try again.",
        retry: true,
        examples: false,
      };
    case "unavailable":
      return {
        title: "Analysis is unavailable",
        body: "The service cannot run analyses right now. Try again later.",
        retry: true,
        examples: true,
      };
    case "not_found":
      return { title: "Analysis not found", body: "This analysis no longer exists. Try again.", retry: true, examples: false };
    case "failed":
      return {
        title: "The analysis failed",
        body: "Something went wrong while analyzing this job description. Try again.",
        retry: true,
        examples: false,
        code: problem.code,
      };
    case "timed_out":
      return {
        title: "The analysis stopped",
        body: "The analysis stopped responding before it finished. Try again.",
        retry: true,
        examples: false,
      };
    case "poll_timeout":
      return {
        title: "Still running",
        body: "The analysis is taking more than five minutes. Try again in a few minutes to see the result.",
        retry: true,
        examples: false,
      };
    case "network":
      return { title: "Could not reach the service", body: "Check your connection and try again.", retry: true, examples: false };
    case "not_configured":
      return {
        title: "Analysis is not configured",
        body: "This build of the page has no API address. Open one of the examples below.",
        retry: false,
        examples: true,
      };
  }
}

export function problemFrom(error: unknown): Problem {
  return error instanceof ApiProblem ? error.problem : { kind: "network" };
}

export function isAbort(error: unknown): boolean {
  return typeof error === "object" && error !== null && (error as { name?: unknown }).name === "AbortError";
}
