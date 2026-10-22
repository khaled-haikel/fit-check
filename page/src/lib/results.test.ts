import { describe, expect, it } from "vitest";
import type { Requirement, RequirementType, Verdict } from "../api/types";
import { groupByVerdict } from "./results";

function requirement(id: string, type: RequirementType, verdict: Verdict | null): Requirement {
  return { id, source: id, text_en: id, type, category: "skill", verdict, reason: null, downgraded: false, citations: [] };
}

describe("groupByVerdict", () => {
  it("orders verdicts meets, partial, does not meet, with must before nice", () => {
    const groups = groupByVerdict([
      requirement("r01", "nice", "does_not_meet"),
      requirement("r02", "must", "partial"),
      requirement("r03", "nice", "meets"),
      requirement("r04", "must", "meets"),
      requirement("r05", "must", "does_not_meet"),
      requirement("r06", "nice", "partial"),
      requirement("r07", "must", "meets"),
    ]);
    expect(groups.map((group) => [group.verdict, group.requirements.map((r) => r.id)])).toEqual([
      ["meets", ["r04", "r07", "r03"]],
      ["partial", ["r02", "r06"]],
      ["does_not_meet", ["r05", "r01"]],
    ]);
  });

  it("leaves out empty groups and requirements not judged yet", () => {
    const groups = groupByVerdict([requirement("r01", "must", null), requirement("r02", "must", "partial")]);
    expect(groups.map((group) => group.verdict)).toEqual(["partial"]);
  });
});
