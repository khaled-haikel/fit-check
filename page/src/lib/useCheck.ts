// The state of one analysis from the page: start it, follow its progress, stop when the page goes away.
import { useCallback, useEffect, useRef, useState } from "react";
import { apiClient, type ApiClient } from "../api/client";
import { pollCheck } from "../api/poll";
import { isAbort, problemFrom, type Problem } from "../api/problems";
import type { Check, CheckStatus } from "../api/types";

export type CheckState =
  | { phase: "idle" }
  | { phase: "running"; status: CheckStatus | "submitting"; check: Check | null }
  | { phase: "done"; check: Check }
  | { phase: "error"; problem: Problem };

const defaultClient = apiClient(import.meta.env.VITE_API_URL);

export function useCheck(client: ApiClient = defaultClient) {
  const [state, setState] = useState<CheckState>({ phase: "idle" });
  const controller = useRef<AbortController | null>(null);

  // Leaving the page (or this component) stops the polling.
  useEffect(() => () => controller.current?.abort(), []);

  const submit = useCallback(
    async (jobDescription: string) => {
      controller.current?.abort();
      const current = new AbortController();
      controller.current = current;
      setState({ phase: "running", status: "submitting", check: null });
      try {
        const started = await client.start(jobDescription, current.signal);
        setState({ phase: "running", status: "queued", check: null });
        const check = await pollCheck({
          checkId: started.checkId,
          intervalMs: started.pollAfterMs,
          signal: current.signal,
          get: client.get,
          onUpdate: (update) => setState({ phase: "running", status: update.status, check: update }),
        });
        setState({ phase: "done", check });
      } catch (error) {
        if (isAbort(error)) return;
        setState({ phase: "error", problem: problemFrom(error) });
      }
    },
    [client],
  );

  return { state, submit };
}
