// POST /checks and GET /checks/{id}, with every non-success answer turned into an ApiProblem.
import { ApiProblem, isAbort, type ProblemKind } from "./problems";
import type { Check, Started } from "./types";

export const DEFAULT_POLL_MS = 3000;
const REFUSALS: ProblemKind[] = ["daily_limit", "monthly_budget", "busy"];

export type FetchLike = (input: string, init?: RequestInit) => Promise<Response>;

export interface ApiClient {
  start(jobDescription: string, signal?: AbortSignal): Promise<Started>;
  get(checkId: string, signal?: AbortSignal): Promise<Check>;
}

interface Answer {
  status: number;
  body: unknown;
}

function errorCode(body: unknown): string | undefined {
  if (typeof body === "object" && body !== null && typeof (body as { error?: unknown }).error === "string") {
    return (body as { error: string }).error;
  }
  return undefined;
}

async function request(fetchFn: FetchLike, url: string, init: RequestInit): Promise<Answer> {
  let response: Response;
  try {
    response = await fetchFn(url, init);
  } catch (error) {
    if (isAbort(error)) throw error;
    // A rejected fetch is a network failure or a response without CORS headers.
    throw new ApiProblem({ kind: "network" });
  }
  let body: unknown;
  try {
    body = await response.json();
  } catch {
    body = null;
  }
  return { status: response.status, body };
}

function refusal(answer: Answer): ApiProblem {
  if (answer.status === 429) {
    const code = errorCode(answer.body);
    const kind = REFUSALS.find((refused) => refused === code);
    return new ApiProblem({ kind: kind ?? "rate_limited" });
  }
  return new ApiProblem({ kind: "unavailable" });
}

export function apiClient(baseUrl: string | undefined, fetchFn: FetchLike = (input, init) => fetch(input, init)): ApiClient {
  const base = (baseUrl ?? "").replace(/\/+$/, "");

  return {
    async start(jobDescription, signal) {
      if (!base) throw new ApiProblem({ kind: "not_configured" });
      const answer = await request(fetchFn, `${base}/checks`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ job_description: jobDescription }),
        signal,
      });
      if (answer.status === 200 || answer.status === 202) {
        const body = answer.body as { check_id: string; poll_after_ms?: number };
        return { checkId: body.check_id, pollAfterMs: body.poll_after_ms ?? DEFAULT_POLL_MS };
      }
      if (answer.status === 400) throw new ApiProblem({ kind: "invalid" });
      throw refusal(answer);
    },

    async get(checkId, signal) {
      if (!base) throw new ApiProblem({ kind: "not_configured" });
      const answer = await request(fetchFn, `${base}/checks/${encodeURIComponent(checkId)}`, { signal });
      if (answer.status === 200) return answer.body as Check;
      if (answer.status === 400 || answer.status === 404) throw new ApiProblem({ kind: "not_found" });
      throw refusal(answer);
    },
  };
}
