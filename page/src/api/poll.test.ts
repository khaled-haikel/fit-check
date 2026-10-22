import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { POLL_LIMIT_MS, pollCheck } from "./poll";
import { ApiProblem } from "./problems";
import type { Check, CheckStatus } from "./types";

function check(status: CheckStatus, error: string | null = null): Check {
  return {
    check_id: "abc",
    status,
    created_at: "2026-10-21T12:00:00Z",
    updated_at: "2026-10-21T12:00:00Z",
    analysis_version: "v",
    model: "m",
    index_version: "i",
    requirements: [],
    score: null,
    usage: { cost_usd: 0, latency_ms: null },
    error,
  };
}

function sequence(...statuses: Check[]) {
  let call = 0;
  return vi.fn(async () => statuses[Math.min(call++, statuses.length - 1)]);
}

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("pollCheck", () => {
  it("stops at done and reports every step", async () => {
    const get = sequence(check("queued"), check("extracting"), check("judging"), check("done"));
    const seen: string[] = [];
    const result = pollCheck({ checkId: "abc", intervalMs: 3000, get, onUpdate: (c) => seen.push(c.status) });
    await vi.runAllTimersAsync();
    await expect(result).resolves.toMatchObject({ status: "done" });
    expect(seen).toEqual(["queued", "extracting", "judging", "done"]);
    expect(get).toHaveBeenCalledTimes(4);
  });

  it("stops at failed with the error code", async () => {
    const get = sequence(check("extracting"), check("failed", "bedrock_error"));
    const result = pollCheck({ checkId: "abc", intervalMs: 3000, get });
    const caught = result.catch((error: unknown) => error);
    await vi.runAllTimersAsync();
    const error = await caught;
    expect(error).toBeInstanceOf(ApiProblem);
    expect((error as ApiProblem).problem).toEqual({ kind: "failed", code: "bedrock_error" });
    expect(get).toHaveBeenCalledTimes(2);
  });

  it("reports a stale analysis as timed out", async () => {
    const result = pollCheck({ checkId: "abc", intervalMs: 3000, get: sequence(check("failed", "timed_out")) });
    await expect(result).rejects.toMatchObject({ problem: { kind: "timed_out" } });
  });

  it("gives up after five minutes", async () => {
    const get = sequence(check("judging"));
    const result = pollCheck({ checkId: "abc", intervalMs: 3000, get });
    const caught = result.catch((error: unknown) => error);
    await vi.advanceTimersByTimeAsync(POLL_LIMIT_MS + 3000);
    expect(((await caught) as ApiProblem).problem.kind).toBe("poll_timeout");
    expect(get).toHaveBeenCalledTimes(POLL_LIMIT_MS / 3000 + 1);
    await vi.advanceTimersByTimeAsync(60_000);
    expect(get).toHaveBeenCalledTimes(POLL_LIMIT_MS / 3000 + 1);
  });

  it("keeps polling through one lost answer", async () => {
    let call = 0;
    const get = vi.fn(async () => {
      call += 1;
      if (call === 2) throw new ApiProblem({ kind: "network" });
      return call < 3 ? check("judging") : check("done");
    });
    const result = pollCheck({ checkId: "abc", intervalMs: 3000, get });
    await vi.runAllTimersAsync();
    await expect(result).resolves.toMatchObject({ status: "done" });
  });

  it("stops when the page aborts it", async () => {
    const controller = new AbortController();
    const get = sequence(check("judging"));
    const result = pollCheck({ checkId: "abc", intervalMs: 3000, get, signal: controller.signal });
    const caught = result.catch((error: unknown) => error);
    await vi.advanceTimersByTimeAsync(1000);
    controller.abort();
    expect(((await caught) as Error).name).toBe("AbortError");
    await vi.advanceTimersByTimeAsync(60_000);
    expect(get).toHaveBeenCalledTimes(1);
  });
});
