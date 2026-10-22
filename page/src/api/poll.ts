// Polls GET /checks/{id} until the analysis is done or failed, for at most five minutes.
import { ApiProblem, type ProblemKind } from "./problems";
import type { Check } from "./types";

export const POLL_LIMIT_MS = 5 * 60 * 1000;
// A throttled or lost poll is retried; this many in a row stop the polling.
const TRANSIENT: ProblemKind[] = ["network", "rate_limited", "unavailable"];
const MAX_TRANSIENT_IN_A_ROW = 3;

export interface PollOptions {
  checkId: string;
  intervalMs: number;
  maxMs?: number;
  signal?: AbortSignal;
  get: (checkId: string, signal?: AbortSignal) => Promise<Check>;
  // Called with every check read, so the page can show progress and requirements early.
  onUpdate?: (check: Check) => void;
}

export function sleep(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    const aborted = () => reject(new DOMException("Polling stopped", "AbortError"));
    if (signal?.aborted) return aborted();
    const timer = setTimeout(() => {
      signal?.removeEventListener("abort", onAbort);
      resolve();
    }, ms);
    const onAbort = () => {
      clearTimeout(timer);
      aborted();
    };
    signal?.addEventListener("abort", onAbort, { once: true });
  });
}

export async function pollCheck(options: PollOptions): Promise<Check> {
  const started = Date.now();
  const maxMs = options.maxMs ?? POLL_LIMIT_MS;
  let transientInARow = 0;
  for (;;) {
    let check: Check | null = null;
    try {
      check = await options.get(options.checkId, options.signal);
      transientInARow = 0;
    } catch (error) {
      const transient = error instanceof ApiProblem && TRANSIENT.includes(error.problem.kind);
      if (!transient || ++transientInARow >= MAX_TRANSIENT_IN_A_ROW) throw error;
    }
    if (check) {
      options.onUpdate?.(check);
      if (check.status === "done") return check;
      if (check.status === "failed") {
        throw new ApiProblem(check.error === "timed_out" ? { kind: "timed_out" } : { kind: "failed", code: check.error ?? undefined });
      }
    }
    if (Date.now() - started >= maxMs) throw new ApiProblem({ kind: "poll_timeout" });
    await sleep(options.intervalMs, options.signal);
  }
}
