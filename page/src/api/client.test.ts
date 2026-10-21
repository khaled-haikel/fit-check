import { describe, expect, it } from "vitest";
import { apiClient, type FetchLike } from "./client";
import { ApiProblem, describeProblem, type Problem, type ProblemKind } from "./problems";

function answering(status: number, body: unknown): FetchLike {
  return async () => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

async function problemOf(promise: Promise<unknown>): Promise<Problem> {
  try {
    await promise;
  } catch (error) {
    if (error instanceof ApiProblem) return error.problem;
    throw error;
  }
  throw new Error("expected an ApiProblem");
}

const JOB = "x".repeat(300);

describe("POST /checks answers", () => {
  it("starts with the check id and the poll interval", async () => {
    const client = apiClient("https://api.example/", answering(202, { check_id: "abc", status: "queued", poll_after_ms: 3000 }));
    await expect(client.start(JOB)).resolves.toEqual({ checkId: "abc", pollAfterMs: 3000 });
  });

  it("starts from a cached answer with the default interval", async () => {
    const client = apiClient("https://api.example", answering(200, { check_id: "abc", status: "done", cached: true }));
    await expect(client.start(JOB)).resolves.toEqual({ checkId: "abc", pollAfterMs: 3000 });
  });

  it.each<[number, unknown, ProblemKind]>([
    [429, { error: "daily_limit" }, "daily_limit"],
    [429, { error: "monthly_budget" }, "monthly_budget"],
    [429, { error: "busy" }, "busy"],
    [429, { message: "Too Many Requests" }, "rate_limited"],
    [400, { error: "invalid request" }, "invalid"],
    [503, { error: "analysis is unavailable" }, "unavailable"],
    [500, null, "unavailable"],
  ])("maps %i %j to %s", async (status, body, kind) => {
    const client = apiClient("https://api.example", answering(status, body));
    expect((await problemOf(client.start(JOB))).kind).toBe(kind);
  });

  it("maps a rejected fetch to a network problem", async () => {
    const client = apiClient("https://api.example", async () => {
      throw new TypeError("Failed to fetch");
    });
    expect((await problemOf(client.start(JOB))).kind).toBe("network");
  });

  it("refuses to start without an API address", async () => {
    expect((await problemOf(apiClient(undefined).start(JOB))).kind).toBe("not_configured");
  });
});

describe("GET /checks/{id} answers", () => {
  it("maps 404 to not found", async () => {
    const client = apiClient("https://api.example", answering(404, { error: "check not found" }));
    expect((await problemOf(client.get("abc"))).kind).toBe("not_found");
  });
});

describe("messages", () => {
  it("send limit and budget refusals to the examples, without a retry", () => {
    for (const kind of ["daily_limit", "monthly_budget"] as const) {
      const message = describeProblem({ kind });
      expect(message.examples).toBe(true);
      expect(message.retry).toBe(false);
    }
    expect(describeProblem({ kind: "daily_limit" }).body).toContain("tomorrow");
    expect(describeProblem({ kind: "busy" }).body).toContain("few minutes");
  });

  it("offer a retry for invalid input, an unavailable service and failed or timed out analyses", () => {
    for (const kind of ["invalid", "unavailable", "failed", "timed_out", "poll_timeout", "network"] as const) {
      expect(describeProblem({ kind }).retry).toBe(true);
    }
    expect(describeProblem({ kind: "failed", code: "bedrock_error" }).code).toBe("bedrock_error");
  });
});
